# Linux MMU port to the SPTM UAT API — scoping

Once the boot shim drives SPTM to `enforce_after` (see `sptm-research.md`),
Linux runs as `EXEC_MODE_XNU_DEFAULT` and **every page-table mutation must go
through `sptm_uat_*` calls**. This doc scopes that port: which SPTM entry
points exist, which Linux operations they replace, where the changes land in
`arch/arm64/`, and a realistic work envelope.

The port is gated by **`CONFIG_ARM64_APPLE_SPTM`**. When off, the kernel
builds identically to today and runs on pre-SPTM hardware (M1/M2/M3 ≤15.x)
unchanged. When on, every PT-write site is wrapped.

## Current scaffold status — compile-only, pre-hardware

Scaffold lives in **`linux-sptm/`** in this repo:

- `linux-sptm/arch/arm64/include/asm/sptm.h` — API surface: packed call-
  number macros for all labeled subsys-0 ops + all of subsys 0xb,
  `sptm_frame_type` and `sptm_exec_mode` enums (names confirmed from SPTM
  binary; values TODO), wrapper-function declarations
- `linux-sptm/arch/arm64/mm/sptm.c` — wrapper layer: raw `sptm_call()`
  GENTER stub (inline-asm, verified disassembly: `mov x16, x0; ...; .word
  0x00201420; ret`), `sptm_boot_handoff()` issuing `(0x0:0xf)`, plus
  HIGH-confidence wrappers (retype, set_pte, switch_root, register_cpu,
  uat_unmap_table)
- `scripts/apply-linux-sptm.py` — idempotent installer into a kernel tree;
  adds `CONFIG_ARM64_APPLE_SPTM` and the Makefile obj-y line

**Verified to build** against linux-asahi HEAD (Linux 7.0, asahi-wip):

```
cd build/linux-asahi
LLVM=1 ARCH=arm64 make defconfig
echo CONFIG_ARM64_APPLE_SPTM=y >> .config
LLVM=1 ARCH=arm64 make olddefconfig
LLVM=1 ARCH=arm64 make arch/arm64/mm/sptm.o   # ✅ clean
```

The matching m1n1 stub also builds (see `m1n1-patches/0004-*.patch` and
`scripts/apply-m1n1-patches.py`): `build/m1n1/build/m1n1.macho` (884 KB)
links cleanly with `sptm_call` and `sptm_boot_handoff` as exported symbols
in the ELF.

Neither artifact has been tested on hardware. Most of the wrapper bodies
in `sptm.c` are still TODO — see the per-function comments in source.

## SPTM API surface (extracted from `sptm.t8132.release.payload`, 26.5)

All names are real symbols in the binary's strings — see
`analysis/sptm/sptm.t8132.release.payload`. Grouped by what Linux needs them
for.

### Page-table state lifecycle (per address-space, ≈ `struct mm_struct`)

| SPTM call                     | Purpose |
|-------------------------------|---------|
| `sptm_uat_init_state`         | Allocate a per-mm state object (root-table tracking, refcounts) |
| `sptm_uat_destroy_state`      | Tear it down |
| `sptm_uat_get_info`           | Query state-object fields (size, root paddr, mode) |
| `uat_state_get_root_table_paddr` / `_set_root_table_paddr` | Root-table accessors |
| `sptm_surt_alloc` / `sptm_surt_free` | Allocate/free a *sub-page user root table* (TTBR0 root) — Apple's TTBR0 root tables are sub-page (multiple per page); helpers reflect that |

### Page-table mutation (the hot path)

| SPTM call                     | Replaces in Linux |
|-------------------------------|-------------------|
| `sptm_map_page`               | `__set_pte` / `__set_ptes` (leaf PTE install) |
| `sptm_uat_map_table`          | `pmd_populate`, `pud_populate`, `p4d_populate` (install lower-level table) |
| `sptm_uat_unmap_table`        | `pmd_clear`, `pud_clear`, `p4d_clear` |
| `sptm_uat_map_continue`       | Chunked map operations (large region) |
| `sptm_uat_unmap_begin` / `sptm_uat_unmap_continue` | Phased unmap (begin/iterate/finish) — matches how Linux's `__pte_alloc` / `unmap_region` work in chunks |
| `sptm_uat_prepare_fw_unmap_begin` / `_continue` | Firmware-region unmap variant |
| `sptm_update_region`          | Bulk update of a contiguous VA range (used by `__create_pgd_mapping`, `apply_to_pte_range`) |
| `sptm_update_papt`            | Update of the physical-aperture page table (Apple's flat phys map ≈ Linux's `linear_map`) |
| `sptm_disjoint_op` / `sptm_update_disjoint` / `sptm_update_disjoint_multipage` | Batched updates on **non-contiguous** pages — matches `set_ptes` for multiple discontiguous targets |
| `sptm_region_op`              | Generic region op (a switch on op-code; covers map/unmap/update in one entry point) |

### Frame typing (page-role transitions)

| SPTM call                     | Linux site |
|-------------------------------|-----------|
| `sptm_retype`                 | Whenever a page changes role: `XNU_DEFAULT → XNU_PAGE_TABLE` when allocating a PT page (`__pte_alloc`, `pmd_alloc_one`, `pud_alloc_one`, `pgd_alloc`); reverse on freeing. Also `XNU_DEFAULT → XNU_RO` for `mark_rodata_ro`, `XNU_RO → XNU_DEFAULT` for `set_memory_rw` in module/bpf paths |
| `uat_retype_in` / `uat_retype_out` | The retype handlers SPTM invokes (callee-side; Linux just calls `sptm_retype`) |
| `sptm_drop_table_refcnts`     | When tearing down an address space, release SPTM's refcounts on the PT pages |

### ASID / context

| SPTM call                     | Linux site |
|-------------------------------|-----------|
| `sptm_uat_set_ctx_id`         | `cpu_switch_mm` — when switching active mm, bind the new ASID to the state object |
| `sptm_uat_remove_ctx_id`      | `destroy_context` / on ASID rollover |
| `sptm_switch_root`            | The TTBR1/TTBR0 write itself — must go through SPTM, not raw `msr ttbr*_el1` |

Note the DT-property gate: `pmap-max-asids` (must be > 0, ≤ 0x10000). Linux's
`asid_bits` calculation must respect SPTM's bound.

### TLB invalidation

| SPTM call                     | Linux site |
|-------------------------------|-----------|
| `sptm_broadcast_tlbi`         | All `flush_tlb_*` paths: `flush_tlb_all`, `flush_tlb_mm`, `flush_tlb_page`, `flush_tlb_range`, `flush_tlb_kernel_range`. Linux's per-CPU `tlbi` is replaced by a single SPTM call that fans out to all CPUs / the requested ASID set |
| `issue_tlbi_by_addr` / `issue_tlbi_by_asid` | (Internal SPTM helpers — Linux does not call directly) |
| `perform_gmmu_tlbi_if_needed` | (Internal — GPU MMU TLBI is handled by SPTM during retype) |

### Per-CPU lifecycle

| SPTM call                     | Linux site |
|-------------------------------|-----------|
| `sptm_register_cpu`           | `secondary_start_kernel` — register each AP CPU with SPTM before bringing it online |
| `sptm_cpu_init`               | Per-CPU early init |
| `sptm_resume_cpu`             | `cpu_resume` (suspend/resume, hotplug-on) |
| `sptm_n_cpus` / `sptm_cpu_id` | Read-only queries (probably unused — Linux uses MPIDR) |

### IO / IOMMU (mostly already in DART driver)

| SPTM call                     | Site |
|-------------------------------|-----|
| `sptm_register_io_frame`      | When mapping device MMIO into the kernel (`ioremap`) |
| `sptm_init_register_allow_io_range` | One-time bootstrap registration of allowed IO ranges from `/chosen` |
| `sptm_iommu_reg_base` / `sptm_gen3dart_*` / `sptm_t8110dart_*` | DART/IOMMU configuration — likely all consumed inside Apple-DART driver (already lives in `drivers/iommu/apple-dart.c`); needs SPTM-aware variant |
| `sptm_nvme_*`                 | NVMe-specific IOMMU helpers — handled by Apple NVMe driver |

### Out of scope for initial port

- **Hibernation:** `sptm_hib_*` — disable `CONFIG_HIBERNATION` on M4+ initially
- **CPU tracing:** `sptm_cputrace_*` — performance counters, optional
- **Guest mode:** `sptm_guest_*` — Linux runs as the host, not as an SPTM-managed guest (KVM stage-2 is a separate, later question — see Risks)
- **Shared region / commpage:** `sptm_configure_shared_region`, `sptm_set_shared_region`, `sptm_slide_region` — Apple's commpage equivalent. Linux uses VDSO; if any shared region work is needed, scope it after the basics are running

## Where the changes land in `arch/arm64/` (from `tools/linux-ref`)

| File | Size today | What changes |
|------|-----------|--------------|
| `arch/arm64/mm/sptm.c` (**new**) | — | The wrapper layer: `sptm_uat_*` packed-call thunks + helper inlines. Probably 500–1000 LoC |
| `arch/arm64/include/asm/sptm.h` (**new**) | — | API surface, frame-type/perm enums, packed-call macros |
| `arch/arm64/include/asm/pgtable.h` | (`__set_pte`, `__set_ptes`, `*_clear`) | ~50–150 LoC: route PT-mutation primitives through `sptm.h` helpers under the CONFIG. The READ-side (`pte_val`, `pte_present`, …) is unchanged |
| `arch/arm64/mm/mmu.c` | 60 KB | The biggest delta: `__create_pgd_mapping`, `alloc_init_p*d`, kernel-text/rodata setup. Touch sites where it directly writes PT entries; route through SPTM. ~200–500 LoC of changes |
| `arch/arm64/mm/contpte.c` | 21 KB | Contiguous-PTE optimization — every bulk-PTE update site needs SPTM batching (`sptm_update_disjoint_multipage` / `sptm_update_region`). ~100–200 LoC |
| `arch/arm64/mm/pageattr.c` | 11 KB | `set_memory_ro/rw/x/nx`, module text → uses `sptm_retype` + region updates. ~100 LoC |
| `arch/arm64/mm/context.c` | 11 KB | ASID alloc and rollover: bind via `sptm_uat_set_ctx_id` instead of raw TTBR-ASID writes. ~100–200 LoC |
| `arch/arm64/mm/pgd.c` | 1 KB | `pgd_alloc` / `pgd_free` — route through `sptm_surt_alloc/free`. ~50 LoC |
| `arch/arm64/mm/proc.S` | 14 KB | `cpu_do_switch_mm`, `cpu_do_resume` — the raw TTBR writes get replaced by an SPTM call. ~100–200 LoC |
| `arch/arm64/include/asm/tlbflush.h` | (the `flush_tlb_*` family) | Route all `flush_tlb_*` to `sptm_broadcast_tlbi` under CONFIG. ~100 LoC |
| `arch/arm64/mm/fault.c` | 29 KB | Mostly unchanged — page-fault path doesn't directly write PTs, but `do_anonymous_page` / `do_fault` reach via `set_pte_at` which is already routed at the pgtable layer |
| `arch/arm64/kernel/smp.c` | — | `secondary_start_kernel` adds `sptm_register_cpu` |
| `arch/arm64/kernel/cpu_errata.c` / Kconfig | — | New `CONFIG_ARM64_APPLE_SPTM`, depends on SoC selection |
| (kbuild) `arch/arm64/Kbuild`, `arch/arm64/mm/Makefile` | — | Add `sptm.o` |

## LoC envelope estimate

**Total: ~1,500–3,000 lines of new and changed kernel code**, concentrated
in `arch/arm64/mm/` and `arch/arm64/include/asm/`. Most of it is the new
`sptm.c` wrapper plus targeted point-changes at every PT-mutation primitive,
all gated by `CONFIG_ARM64_APPLE_SPTM`.

For calibration against other arm64 features:
- KAISER/KPTI: ~1500 lines
- arm64 LPA2: ~2000 lines
- arm64 MTE: ~3000 lines
- arm64 PAC: ~500 lines

So this is a "significant arm64 feature port" magnitude — comparable to MTE.
Not a small patch, not a multi-year rewrite. The work is parallelizable
(separate areas: pgtable.h, context.c, tlbflush.h, mmu.c).

## Risks & open questions

1. **TLB-invalidate granularity & cost.** Linux's per-CPU `tlbi vmalle1is` is
   cheap; `sptm_broadcast_tlbi` is a GL2 round-trip. Performance impact on
   `munmap`-heavy / fork-heavy workloads is unknown. May need a batching layer
   in the wrapper.

2. **`sptm_retype` cost on PT-page allocation.** Every new PT page costs a GL2
   trip to type it `XNU_PAGE_TABLE`. The fault path may need a per-CPU cache
   of pre-typed PT pages.

3. **Contiguous PTE / hugepage interplay.** arm64's contiguous-PTE optimization
   batches 16/128 entries. SPTM call signatures need to accept these batches
   efficiently — `sptm_update_disjoint_multipage` looks right but signatures
   are unconfirmed. Open RE.

4. **`init_mm` bootstrap order.** Linux sets up `init_mm` and the kernel
   page tables *very* early — before normal allocators are up. SPTM calls
   must be functional at that point. Need to verify against the SPTM
   "after bootstrap, before OS hands off again" state.

5. **KVM stage-2.** Linux KVM uses stage-2 page tables for guests. SPTM has
   `XNU_STAGE2_PAGE_TABLE` / `_ROOT_TABLE`, but exposing KVM through SPTM
   doubles the API surface. **Scope it as a follow-on** — initial port boots
   without KVM, then add KVM later.

6. **GCS (Guarded Control Stack).** New arm64 feature; `arch/arm64/mm/gcs.c`
   exists. Interaction with SPTM's frame types is undefined here. Probably
   also a follow-on.

7. **Permission/`table_permissions` packing.** The `sptm_register_dispatch_table`
   `permissions` argument is opaque packed bits. For the *boot* shim Linux
   doesn't register tables, but for some SPTM operations the wrapper layer may
   need to construct permission values. **Needs decode** from XNU's caller
   side (look at how XNU constructs permissions for its CTRR dispatch table).

8. **Exact `(subsys, idx)` numbers for each `sptm_uat_*`.** I have the symbol
   names; I don't yet have the SPTM call-number for each. The boot handoff is
   `(0x0, 0xf)`. Most UAT operations are likely in subsystem 0 or in a UAT
   subsystem. Decoding the `(subsys, idx) → handler` table at `0x1a770` and
   correlating with the named symbols closes this — it's mechanical RE,
   ~half-day of focused work.

## What's *not* needed (worth restating)

- No new compiler, no clang fork, no toolchain change.
- No new ABI on the Linux-userspace side — userspace doesn't see SPTM.
- No changes outside `arch/arm64/`; generic mm/ is untouched.
- `init_mm` and `init_pg_dir` data structures are unchanged in *layout*; only
  the *writes* into them are routed.
- Drivers are unchanged (DART driver already wraps IOMMU calls; SPTM-DART
  changes are inside `drivers/iommu/apple-dart.c` only).

## The complete `(subsys, idx)` call-number space

Scanned the M4 kernelcache (`com.apple.kernel` fileset) for every `GENTER`
(`udf #0` = `0x00201420`) and reconstructed the preceding `movz`/`movk x16`
sequence for each. Complete dump: `analysis/sptm/sptm-call-numbers.csv`.

**148 unique `(subsys, idx)` pairs across 9 subsystems:**

| subsys | count | idx range | named externally |
|--------|-------|-----------|------------------|
| `0x0`  | 50    | `0x0–0x31`| **1/50** (only `_sptm_retype` = `(0, 1)`) |
| `0x3`  | 19    | `0x0–0x12`| 0/19 |
| `0x5`  | 3     | `0x0–0x2` | 0/3 |
| `0x6`  | 9     | `0x0–0x8` | 0/9 |
| `0x7`  | 13    | `0x0–0xc` | 0/13 |
| `0x9`  | 13    | `0x0–0xc` | **13/13** (cputrace — all `_sptm_cputrace_*`) |
| `0xa`  | 6     | `0x0–0x5` | 0/6 |
| `0xb`  | 19    | `0x0–0x12`| **19/19** (gen3dart/IOMMU — all `_sptm_gen3dart_*`) |
| `0xd`  | 16    | `0x0–0xf` | 0/16 |

So **33 of 148** have a public XNU export name; the remaining 115 are
**XNU-internal inline genter sites** (e.g. compiled into `pmap_arm.c` via
`SPTM_CALL`-style macros), invisible to the kernel's external symbol table.
Their `(subsys, idx)` numbers are known; only their *symbolic name* is not.

### What this means for the port

For the Linux MMU wrapper layer (`arch/arm64/mm/sptm.c`), the integration
pattern per call is:

```c
#define SPTM_PACK(subsys, idx) (((u64)(subsys) << 32) | (u32)(idx))

static inline u64 sptm_call(u64 packed, u64 a, u64 b, u64 c, u64 d)
{
    u64 ret;
    asm volatile("mov x16, %1\n"
                 ".long 0x00201420\n"      /* GENTER */
                 "mov %0, x0\n"
                 : "=r"(ret)
                 : "r"(packed), "r"(a), "r"(b), "r"(c), "r"(d)
                 : "x0","x1","x2","x3","x16","memory");
    return ret;
}

#define sptm_retype(...)   sptm_call(SPTM_PACK(0, 1), __VA_ARGS__)
/* ...one wrapper per (subsys, idx) we use... */
```

### What's known for free vs needs per-call correlation

- **Subsys `0xb` (19 calls)** — fully named, complete signatures recoverable
  from `_sptm_gen3dart_*` stubs in the kernelcache. Plug straight into
  `drivers/iommu/apple-dart.c`'s SPTM-aware path.
- **Subsys `0x9` (13 calls)** — fully named (cputrace). Linux MMU port
  doesn't need these.
- **Subsys `0x0` (50 calls)** — only `_sptm_retype = (0,1)` named. The other
  49 are the **bulk of the UAT page-table API** Linux needs. Per-call name
  correlation requires either:
  1. Walking each XNU genter site in `pmap_arm.c`/`vm_*` and reading the
     surrounding function (its name appears in nearby assertion `__func__`
     strings), or
  2. Decoding SPTM's per-(subsys,idx) dispatch into the named handlers
     inside SPTM's binary (each named SPTM function — `sptm_uat_map_table`,
     `sptm_map_page`, etc. — has assertion strings that locate it; map
     the dispatch input to the handler output).
- **Subsystems `0x3`/`0x5`/`0x6`/`0x7`/`0xa`/`0xd` (66 calls)** — none named.
  These are likely SoC platform / TLBI / hibernation / coprocessor /
  exception-return families. Same correlation strategy needed.

### Estimated correlation effort

Mechanical RE, ~1–2 days of focused work to correlate all 115 unnamed calls
to SPTM-internal names — likely fewer iterations because each XNU genter
site sits inside a function whose `__func__` string (in a nearby assertion)
gives the operation's name directly. For the Linux MMU port, you don't need
*all* 115 — just the subset you actually call from arm64 mm code, probably
~20–30 of them. The CSV at `analysis/sptm/sptm-call-numbers.csv` makes it
trivial to iterate: pick an unnamed `(subsys, idx)`, disassemble the genter
site VA, walk back to the containing function, read its assertion `__func__`.

This is **not blocking** for starting the Linux port. You can begin with the
3 cleanly-named subsystems (`gen3dart` for DART driver, `retype` for page
typing) and add `sptm_uat_*` wrappers as each is identified during
implementation. The CSV is the index.

### Subsys 0 labels (recovered from XNU caller-function names)

For the 50-entry subsys 0 (the bulk of the MMU API), scanned each stub for
BL callers and identified each caller via `__func__` strings inside its
function body. Full data: `analysis/sptm/sptm-subsys0-labels.csv`.

Confidence legend: ✓ confirmed (named export or earlier-RE'd) · H high · M
medium · L low (callers are generic helpers) · ? no XNU callers (not in
Linux's needed surface).

| idx | likely SPTM op | conf. | XNU callers (hint) |
|-----|---------------|-------|--------------------|
| `0x00` | unknown | ? | (no callers) |
| `0x01` | **`sptm_retype`** | ✓ | `pa_get_ptd`, `sptm_get_frame_type` (28 sites) |
| `0x02` | `sptm_map_page` / `sptm_uat_map_table` | M | `pa_get_ptd`, `pmap_set_shared_region` |
| `0x03` | `sptm_uat_get_info` | M | `sptm_get_frame_type`, `pa_get_ptd`, `kvtophys_nofail` |
| `0x04` | `uat_state_get_root_table_paddr` | M+ | `pa_get_ptd` ×3 |
| `0x05` | `sptm_update_region` | M+ | `pmap_set_shared_region` ×8, `pmap_unnest_options` |
| `0x06` | `sptm_drop_table_refcnts` / info-getter | L | `pa_get_ptd` ×5 |
| `0x07` | `sptm_disjoint_op` | L | mixed |
| `0x08` | `sptm_update_disjoint` | L | `pa_get_ptd` |
| `0x09` | **`sptm_configure_shared_region`** | H | `pmap_set_shared_region` |
| `0x0a` | **`sptm_unnest_region`** | H | `pmap_unnest_options_internal` |
| `0x0b` | `sptm_nest_region` / unnest variant | M | mixed |
| `0x0c` | `sptm_set_shared_region` | M | `vm_shared_region_auth_remap`, `pmap_trim_internal` |
| `0x0d` | **`sptm_switch_root`** | H | `pmap_switch_internal` |
| `0x0e` | **CPU topology query** | ✓ | `_ml_get_topology_info` (research doc) |
| `0x0f` | **boot handoff `(0x0:0xf)`** | ✓ | (no runtime callers; raw genter in `__TEXT_BOOT_EXEC`) |
| `0x10` | `sptm_slide_region` / shared | M | `pmap_set_shared_region` |
| `0x11` | `sptm_set_shared_region` (paired) | M | `pmap_set_shared_region` |
| `0x12` | `sptm_register_cpu` / `cpu_init` | H (boot) | `arm_init` |
| `0x13` | `sptm_n_cpus` / `cpu_init` aux | H (boot) | `arm_init` |
| `0x14` | debug/IRQ helper | L | `ml_set_interrupts_enabled_with_debug` ×5 |
| `0x15` | **`sptm_map_page`** (frequent leaf-PTE install) | H | `pa_get_ptd` ×7, `pmap_set_shared_region` ×3 |
| `0x16–0x1d` | (8 idxs) unused / other-kext only | ? | no XNU callers |
| `0x1e` | **`uat_state_get_root_table_paddr`** | H | `uat_get_root_table_paddr` (direct name match) |
| `0x1f` | `sptm_register_io_frame` | M+ | IOKit (io frame registration) |
| `0x20` | `sptm_register_io_frame` (paired/free) | M+ | IOKit (io frame registration) |
| `0x21` | `sptm_init_register_allow_io_range` / `register_xnu_exc_return` | M | `arm_init` |
| `0x22–0x26` | (5 idxs) unused / other-kext only | ? | no XNU callers |
| `0x27` | `sptm_nest_region` | M | `pmap_set_shared_region` |
| `0x28` | `sptm_nest_region` (paired with `0x27`) | M | `pmap_set_shared_region` |
| `0x29–0x2a` | (2 idxs) unused / other-kext only | ? | no XNU callers |
| `0x2b` | **`sptm_uat_unmap_table`** | H | `pmap_remove_options_internal` |
| `0x2c` | **`sptm_unmap_table`** / `unmap_continue` | H | `pmap_remove_options_internal` |
| `0x2d` | debug/IRQ helper | L | `ml_set_interrupts_enabled_with_debug` ×2 |
| `0x2e` | `sptm_register_xnu_exc_return` / `register_cpu` | M+ | `arm_init` |
| `0x2f–0x31` | (3 idxs) unused / other-kext only | ? | no XNU callers |

**Coverage:** 5 confirmed + 12 high + 12 medium + 5 low + 19 not-needed.
Linux's MMU port surface from subsys 0 is roughly **the 24 confirmed/high/
medium-confidence rows** (the others are either already named, debug, or
not used by XNU and so not by Linux either). The 19 zero-caller idxs do
not appear in the kernel's `__TEXT_EXEC`; they are not part of the API a
Linux port needs to implement.

The low-confidence and medium-confidence labels are best refined during
implementation — when you write a wrapper for, say, `sptm_uat_map_table`,
you'll know which exact `(0, idx)` it is by reading the XNU caller-side
context for that operation (its function-body genter site, and which
`pmap_*` function it sits inside).
