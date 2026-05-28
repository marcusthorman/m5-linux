# SPTM Research Notes

## What is SPTM

Secure Page Table Monitor. Runs at GL2 — a privilege level above EL2
(hypervisor) and EL1 (kernel). Manages ARM64 page tables from above the OS.

Apple's stated purpose: prevent even a compromised kernel or hypervisor from
modifying page table mappings, closing a class of privilege escalation.

### Chip coverage — SPTM is NOT M4-exclusive (corrected)

Earlier notes said "introduced with M4." That is wrong for the macOS 26.5
(25F71) firmware. The `BuildManifest.plist` lists `Ap,SecurePageTableMonitor`
(and `Ap,RestoreSecurePageTableMonitor`) as **signed boot components** — loaded
and verified by iBoot at boot — for these chips:

| Has SPTM boot object (25F71) | No SPTM |
|------------------------------|---------|
| M1 Pro/Max/Ultra (t6000-2), M2 (t8112), M2 Pro/Max/Ultra (t6020-2), M3 (t8122), M3 Pro/Max (t6030/1), M4 family (t8132/t6040/1), A18 Pro (t8140), M5 family (t8142/t6050) | **M1 base only (t8103)** |

So under current macOS the SPTM blocker is *not* unique to M4 — it is boot-loaded
on M2 and M3 too. The only listed Apple Silicon Mac chip without SPTM is the
original M1 (t8103), which is exactly the platform Asahi's hypervisor RE was
built on.

Two caveats this firmware alone can't resolve: (1) "boot-loaded" is proven from
the manifest, but whether SPTM *enforces* the same restrictions that break the
m1n1 hypervisor on M2/M3 as it does on M4 is not established here — it may be
why Asahi's M2/M3 support (built against earlier macOS) still works. (2) The
full **Exclaves/ExclaveOS** secure world is newer: `Ap,ExclaveOS*` appears as a
boot component only for A18 Pro, M5, and M5 Pro — not M3/M4 — though every SPTM
build (incl. M3) contains the XNU↔TXM transition code.

Practical upshot for this project: the SPTM protocol RE and the XNU-shim below
are the **same binary/version on M2/M3/M4/M5** (see below), so the work targets
all of them, not just M4.

## Why it blocks Linux

The Asahi m1n1 hypervisor works by running XNU as a guest under m1n1 at EL2,
then intercepting MMIO accesses to reverse-engineer hardware. SPTM breaks this:

1. **Boot environment incompatibility**: SPTM is active at GL2 when the boot
   object runs. SPTM expects EL2 + MMU enabled to configure page tables.
   Standard Linux boot protocol expects EL2 + MMU disabled. Incompatible.

2. **Hypervisor mode broken**: SPTM intercepts XNU's page table operations.
   m1n1 cannot transparently observe macOS anymore. Running macOS as a guest
   under m1n1 is no longer possible.

## Known workaround strategy: XNU shim

Proposed by Sven Peter (CCC December 2025). No public implementation yet.

Implement a minimal stub in m1n1 that:
1. Enters the EL2+MMU environment SPTM requires
2. Makes the minimum SPTM calls to satisfy initialization
3. Gets SPTM into a state where a Linux handoff is accepted
4. Jumps to the Linux boot entry point

The static RE below maps the minimum call set needed.

---

## SPTM binary — accessible (the unblock)

The SPTM binary ships in the IPSW as a per-chip firmware blob and is **not
encrypted**. This means the boot protocol can be reversed from the callee side
directly, not just inferred from XNU. Extract with `analysis/sptm/extract-sptm.sh`.

| Path | Chip | Notes |
|------|------|-------|
| `Firmware/sptm.t8132.release.im4p` | M4      | present |
| `Firmware/sptm.t6041.release.im4p` | M4 Max  | present (no t6040 / M4 Pro blob) |
| `Firmware/sptm.t8142.release.im4p` | M5      | present |
| `Firmware/sptm.t6050.release.im4p` | M5 Pro  | present (no t6051 / M5 Max) |
| `Firmware/txm.macosx.release.im4p` | all     | Trusted Execution Monitor (single) |

- **im4p payload is LZFSE-compressed, no KBAG** → decompresses to a plain
  **ARM64e `MH_EXECUTE` Mach-O** (~1.2 MB). PIE, NoUndefs, stripped (1 symbol).
- **Source version 611.120.6 across M3, M4 and M5** (verified on t8122 M3,
  t6030 M3 Pro, t6031 M3 Max, t8132 M4, t8142 M5, t6050 M5 Pro — all 611.120.6;
  per-chip binaries differ only in chip constants, e.g. M3 Pro vs M4 first differ
  at byte 145). Same `dispatch_state_machine`, `sptm_register_dispatch_table`,
  `XNU->TXM`, UAT bootstrap strings in the M3 binary. The boot protocol is
  therefore the same M3→M5 — a shim validated on M4 should carry to both M5
  and M3. Addresses differ per build (e.g. dispatcher code shifts), so RE must
  be re-anchored per binary. M5 Pro (t6050) is larger (1.37 MB), likely
  multi-die handling.
- Layout: `__TEXT_EXEC.__text` ≈ 381 KB of code; entry `0xfffffff0270ac388`
  (T8132). Boot structures live in `__BOOTDATA` (80 KB) and `__LATE_CONST`.

### What the strings already tell us (callee side)

The binary carries assertion/function-name strings (not symbols) that sketch the
boot path and the validation rules — directly relevant to the shim's open
questions:

- `uat_bootstrap_parse_dt` — **SPTM parses the device tree at bootstrap.**
- `handoff_region` with fields `micro_magic`, `powered_off` — the SPTM↔XNU
  handoff structure (the callee-side view of XNU's all-zero `__DATA_SPTM`).
- `[SPTM] Synchronous exception taken from guest before XNU bootstrap`,
  `[PANIC DURING BOOTSTRAP]`, `[SK BOOTSTRAP PANIC]` — SPTM has an explicit
  pre-XNU-bootstrap phase with its own fault handling.
- A **UAT** (Unified Address Translation) subsystem: `sptm_uat_init_state`,
  `sptm_uat_map_table`, `sptm_retype`, `uat_retype_in/out`, and a wall of
  `VIOLATION_UAT_INVALID_{TYPE,SUBTYPE,ROOT_TABLE,VADDR,PADDR,PT_LEVEL,REFCNT,
  …}` checks — the page-table-frame invariants SPTM enforces on every call.

### GENTER dispatch internals (T8132, no slide; PIE base `0xfffffff027004000`)

Reset entry `0xfffffff0270ac388` is just `b 0xfffffff0270af000`. The bootstrap
(`0xfffffff0270af000`) runs **under iBoot, before m1n1** — it takes the SPTM
boot-args ptr in x0, checks args version ≥ 3, stamps a log struct at
`bootargs+0x480` with magic **"SPTM"** v3, sets its own `vbar_el1 =
0xfffffff0270ad000`, and configures `HCR_EL2`/`HCRX_EL2` if entered at EL2. The
shim does NOT run this; it calls the already-initialized monitor via `genter`.

SPTM installs its guarded-entry vectors (`0xfffffff0270af8a8` region): a
temporary dispatcher, then it `genter`s itself (the `udf #0` = GENTER at
`0xfffffff0270af8d4`) and installs the **runtime dispatcher**:
- `gxf_entry_el1  = 0xfffffff0270a454c`  ← where a shim `genter` from EL1 lands
- `vbar_gl1       = 0xfffffff0270a8000`  (GL2 exception vectors)
- `gxf_pabentry_el1 = 0xfffffff0270bbf10` (GL2 prefetch-abort handler)
- runtime `gxf_entry_el12` is set later at `0xfffffff0270ecf78` (EL2/virt path)

Dispatcher `0xfffffff0270a454c`:
1. `x8 = esr_gl1 & 0x1f` → entry **type** (0–4); type 4 and a `__DATA+0xc`
   bit-15 flag select alternate paths; unknown types spin (`mov x0,#0xdead;wfe`).
2. Per-CPU GL2 state via `tpidr_gl2`; save area at `+0xa30`, with a recursion/
   mode counter at `+0x38` that picks save slot `+0x80` vs `+0x138`.
3. Types 2 and 3 (the normal SPTM-call paths) save XNU's full context: args
   **x0–x7 at save+0x40**, callee-saved + **x15/x16 (the call number) at
   save+0x90**, plus `spsr_gl1`, `sp_el0`, x17 — then switch to the GL2 stack
   (`[state+0x20]`) and tail-call a common trampoline **`0xfffffff0270ec010`**
   with the entry type in x0 (3) and a packed value in x1.

The trampoline `0xfffffff0270ec010` is **`dispatch_state_machine`** — and this
reframes everything (see below).

### SPTM is a security-domain state machine, not a flat syscall table

`0xfffffff0270ec010` is the state-machine dispatcher. Its error strings name the
model directly: `dispatch_state_machine`, `state_to_string`, `invalid state`,
`invalid event type`, `invalid next state`, and `invalid state transition - no
action set for the transition. current_state: %s, event_type: %s,
event_metadata: %#llx`. So a "call" is an **(state, event) → action/next-state**
transition, keyed per **caller_domain** (`cpu_data->logical_id`,
`event_metadata`).

Dispatch tables are **registered at runtime, not hardcoded**:
`sptm_register_dispatch_table(table_id, …)` and
`register_sptm_iommu_dispatch_table`. Each entry is
`{ dispatch_entry_point, permissions }` and SPTM validates the target against
per-domain permissions — `[SPTM Dispatch] Found illegal dispatch entry point.
caller_domain: %d, entry_point: %#llx, dispatch_target: %#llx`. The table seen
at `0xfffffff02701a770` (32-byte entries `{type, handler@+8 chained-fixup,
flags, mask}`; e.g. handlers `0xfffffff0270e8624/0e8638`) is one such table.

There are at least three domains: **XNU ↔ SPTM ↔ TXM**. SPTM guards transitions
between them — `Invalid hop detected when transitioning XNU->TXM`, `txm_stack`,
`phys_to_type(txm_stack)` — which is why TXM ships as its own monitor
(`txm.macosx.release.im4p`). Also present: `sptm_register_xnu_exc_return` /
`xnu_exc_return_handler` (SPTM owns XNU's exception-return path).

**Implication for the shim:** the boot handoff is not "make one call." To stand
in for XNU, the boot object must register a valid dispatch table (entry points +
permissions SPTM will accept) and drive the state machine from its bootstrap
state into the running state. `(0x0:0xf)` is most likely the *event* that
performs that registration / transition.

**Next targets, in order:**
1. `sptm_register_dispatch_table` — find it (string xref at `0xfffffff027012d32`
   region), learn the table_id space, entry format, and permission model.
2. The state/event table behind `dispatch_state_machine` — enumerate states,
   events, and which transition `(0x0:0xf)` drives.
3. Correlate the registered XNU table + `handoff_region`
   (`micro_magic`/`powered_off`) with XNU's zeroed `__DATA_SPTM`.

---

## SPTM calling convention

All runtime SPTM calls use a two-instruction sequence:

```asm
mov     x16, #(subsys << 32 | idx)   ; pack call number
.long   0x00201420                   ; GENTER — enter GL2
```

The `0x00201420` word disassembles as `udf #0` in tools that don't know Apple's
ISA, but it is **`GENTER`** — Apple's Guarded-ENTER instruction (the same
encoding m1n1 already uses in `gxf_asm.S`). It is NOT an undefined-instruction
trap. GENTER switches to GL2 and runs SPTM's resident `GXF_ENTER` handler, which
dispatches on x16: subsystem in the upper 32 bits, call index in the lower 32.
SPTM does its work and `GEXIT`s back to the instruction after the GENTER, so
from the caller's side a call returns like a normal `bl`.

Boot-time calls (in `__TEXT_BOOT_EXEC`) use the same GENTER but without the
pre/post hook wrappers — a direct `mov x16 / genter` pair.

---

## XNU SPTM stub map

Extracted from `kernelcache.release.Mac16,1_2_3_10_12_13` (M4, macOS 26.5
25F71). Stubs live in `__TEXT_EXEC,__text`. There are 9 active subsystems.

### Subsystem 0x0 — Core XNU/SPTM interface

Stubs start at `0xfffffe000c066758`, 40-byte stride. Named symbols confirmed:

| idx  | VA                   | Symbol (if known)     | BL callers |
|------|----------------------|-----------------------|------------|
| 0x00 | 0xfffffe000c066758   |                       | 1          |
| 0x01 | 0xfffffe000c066780   | `_sptm_retype`        | 5          |
| 0x02 | 0xfffffe000c0667a8   |                       | 5          |
| 0x03 | 0xfffffe000c0667d0   |                       | 3          |
| 0x04 | 0xfffffe000c0667f8   |                       | 3          |
| 0x05 | 0xfffffe000c066820   |                       | 5          |
| 0x06 | 0xfffffe000c066848   |                       | 5          |
| 0x07 | 0xfffffe000c066870   |                       | 3          |
| 0x08 | 0xfffffe000c066898   |                       | 1          |
| 0x09 | 0xfffffe000c0668c0   |                       | 1          |
| 0x0a | 0xfffffe000c0668e8   |                       | 1          |
| 0x0b | 0xfffffe000c066910   |                       | 1          |
| 0x0c | 0xfffffe000c066938   |                       | 2          |
| 0x0d | 0xfffffe000c066960   |                       | 5          |
| 0x0e | 0xfffffe000c066988   | CPU topology query    | 1 (from `_ml_get_topology_info`) |
| 0x0f | 0xfffffe000c0669b0   |                       | 1          |
| 0x10 | 0xfffffe000c0669d8   |                       | 5          |
| 0x16 | 0xfffffe000c066ac8   |                       | 1          |
| 0x19 | 0xfffffe000c066b40   |                       | 1          |
| 0x1a | 0xfffffe000c066b68   |                       | 1          |
| 0x1b | 0xfffffe000c066b90   |                       | 1          |
| 0x1c | 0xfffffe000c066bb8   |                       | 1          |
| 0x1d | 0xfffffe000c066be0   |                       | 5          |
| 0x1e | 0xfffffe000c066c08   |                       | 1          |
| 0x25 | 0xfffffe000c066d20   |                       | 1          |
| 0x26 | 0xfffffe000c066d48   |                       | 1          |
| 0x27 | 0xfffffe000c066d70   |                       | 1          |
| 0x29 | 0xfffffe000c066dc0   |                       | 1          |
| 0x2a | 0xfffffe000c066de8   |                       | 1          |
| 0x2b | 0xfffffe000c066e10   |                       | 1          |
| 0x2c | 0xfffffe000c066e38   |                       | 1          |

Note: idx 0x0f is the **boot handoff call** — see Boot Handoff Sequence below.
Its stub wrapper exists but has zero runtime BL callers; the boot code uses the
raw `mov x16, #0xf; udf #0` form directly.

### Subsystem 0x3

First stub: `0xfffffe000c0670cc`, 44-byte stride. 19 stubs total.
BL callers of stub[0]: `[0xfffffe000b85a344]` (1 call site).

### Subsystem 0x5

First stub: `0xfffffe000c067a6c`. 3 stubs.
BL callers of stub[0]: `[0xfffffe000b8f1610]` (1 call site).

### Subsystem 0x6

First stub: `0xfffffe000c066f40`. 9 stubs.
BL callers of stub[0]: `[0xfffffe000b8e5edc]` (1 call site).

### Subsystem 0x7

First stub: `0xfffffe000c067a98`. 13 stubs.
BL callers of stub[0]: `[0xfffffe000b8c9ce4]` (1 call site).

### Subsystem 0x9 — cputrace

First stub: `0xfffffe000c067d24`. 13 stubs. Named symbol confirmed:
- `_sptm_cputrace_is_mode_supported` = (0x9, 0x00) at `0xfffffe000c067d24`

### Subsystem 0xa

First stub: `0xfffffe000c066e38`. 6 stubs.
BL callers of stub[0]: `[0xfffffe000beffdb4]` (1 call site — same VA as subsys
0 idx 0x2c; these may overlap or share an entry point).

### Subsystem 0xb — gen3dart (DART IOMMU)

First stub: `0xfffffe000c0676d0`. 19 stubs. Named symbols:
- `_sptm_gen3dart_map_table` = (0xb, 0x00) at `0xfffffe000c0676d0`
- `_sptm_gen3dart_init`      = (0xb, 0x06) at `0xfffffe000c0677d8`

DART = Device Address Resolution Table — Apple's IOMMU. This subsystem controls
DART table management from GL2. Important for DMA-capable peripherals.

### Subsystem 0xd

First stub: `0xfffffe000c067410`. 16 stubs.
BL callers of stub[0]: `[0xfffffe000b8f3b30, 0xfffffe000b8f3bc0]` (2 call
sites — likely a pair of init/deinit or map/unmap operations).

---

## Boot handoff sequence

The single boot-time SPTM call is in `__TEXT_BOOT_EXEC` on the BSP path,
immediately after setting the exception vector base:

```asm
; BSP entry point offset in __TEXT_BOOT_EXEC (VA 0xfffffe000c09c040):

0xfffffe000c09c068:  bl   sub_fffffe000c0a0488   ; KASLR / Mach-O header check
0xfffffe000c09c070:  bl   sub_fffffe000c0a09dc   ; static ctor / link table init
0xfffffe000c09c074:  adrp x9, 0xfffffe000b72c000
0xfffffe000c09c078:  add  x9, x9, #0             ; x9 = exception vector table VA
0xfffffe000c09c07c:  msr  vbar_el1, x9           ; set EL1 exception vector base
0xfffffe000c09c080:  isb                          ; instruction sync barrier
0xfffffe000c09c084:  mov  x16, #0xf              ; SPTM call (subsys=0, idx=0xf)
0xfffffe000c09c088:  udf  #0                     ; raw GL2 trap — NO hooks
0xfffffe000c09c08c:  mov  x0, x26               ; restore boot_args ptr
0xfffffe000c09c090:  mov  x1, x27               ; restore boot_args_ext ptr
0xfffffe000c09c094:  b    0xfffffe000b8f1334     ; jump to arm_init
```

**Key observations for the shim:**

1. The call is `(subsys=0, idx=0xf)` — single call number `0xf`.
2. It is made **after** VBAR_EL1 is set — SPTM may read EL1 state on entry.
3. It is made with a raw `udf #0`, NOT through the runtime stub. This avoids
   the pre-hook's `currentg` check (see Runtime Hooks below).
4. After the call returns, execution continues in `arm_init`. There is no
   separate "exit SPTM" call — the GL2 trap is fire-and-forget on this path.
5. The boot-time stub at `0xfffffe000c0669b0` (idx=0xf) has **zero** runtime
   BL callers — it exists in the stub array but is never called at runtime.

---

## Shim entry draft (m1n1 side)

First cut of the m1n1-side entry mechanism for the boot handoff — see
`m1n1-patches/0004-sptm-add-xnu-shim-boot-handoff.patch`. **This does not yet
boot Linux**; it encodes what the static RE established and isolates the
remaining unknowns so each can be tested on hardware.

What it gets right (from RE):
- The call is `GENTER` (`0x00201420`), reusing m1n1's existing encoding — not a
  fake `udf`. m1n1 already has GENTER/GEXIT and GXF plumbing.
- Boot handoff call number is `(0x0, 0xf)`, issued via a bare `mov x16,#0xf;
  genter`. A small `sptm_call(callnum, a,b,c,d)` stub places the number in x16.
- It must **not** call m1n1's `_gxf_init()` — that repoints `GXF_ENTER_EL1` at
  m1n1's own GL2 vectors. The shim relies on iBoot/SPTM's resident `GXF_ENTER`,
  so it issues a bare GENTER. (Caveat: if m1n1's hypervisor has already run
  `gxf_init`, `GXF_ENTER` no longer points at SPTM — the shim must run first or
  restore SPTM's vector. Tracked as a follow-up.)
- Chip guard via `chip_id` (T8132/T6040/T6041/T8142/T6050/T6051); no-op on
  pre-M4 SoCs.

Placement: `main.c` calls `sptm_boot_handoff()` at the next-stage handoff. XNU
makes the call with the **MMU on**, but m1n1's handoff runs **after**
`mmu_shutdown()`. The draft issues the call *before* the shutdowns — a guess.

Open questions blocking a working shim (need SPTM binary RE or hardware):
1. **Pre-state SPTM validates** — exact EL/MMU/register state expected on entry
   to `(0x0:0xf)`. Drives the placement vs. `mmu_shutdown()` question.
2. **Non-XNU boot object** — whether SPTM lets anything but signed XNU proceed
   past the handoff, or whether there is a passthrough/relaxed mode.
3. **Additional required calls** — whether `gen3dart` init / `retype` / others
   must run before a Linux handoff is accepted, or if `(0x0:0xf)` suffices.

---

## Runtime call hooks

Every runtime SPTM stub (i.e., those NOT in `__TEXT_BOOT_EXEC`) wraps the
`mov x16 / genter` pair with two hook calls:

```asm
; Canonical runtime stub (_sptm_retype at 0xfffffe000c066780):
pacibsp
stp   fp, lr, [sp, #-0x10]!
mov   fp, sp
bl    0xfffffe000b72e830    ; pre_hook
mov   x16, #0x1             ; call number
udf   #0
bl    0xfffffe000b72e89c    ; post_hook
mov   sp, fp
ldp   fp, lr, [sp], #0x10
retab
```

### Pre-hook (`0xfffffe000b72e830`)

Saves arguments, increments a call depth counter, and — critically — checks
the `currentg` register:

```asm
0xfffffe000b72e868:  mrs  x14, currentg      ; read Apple GL2 context register
0xfffffe000b72e86c:  cmp  x14, #0
0xfffffe000b72e870:  b.ne loc_fffffe000b72e870   ; INFINITE LOOP if already in GL2
```

`currentg` is an Apple-proprietary ARM64 system register that tracks whether
execution is currently inside a GL2-level call. If it is non-zero, the CPU
spins forever rather than making a nested SPTM call. This prevents re-entrant
SPTM calls from EL1/EL2.

### Post-hook (`0xfffffe000b72e89c`)

Decrements the call depth counter and handles any pending deferred work (e.g.,
TLB shootdowns, IPI delivery) that was deferred while SPTM held GL2.

---

## `__DATA_SPTM` segment

XNU's Mach-O contains a dedicated segment for SPTM-managed data:

```
Segment: __DATA_SPTM
  File offset : 0x018ec000
  File size   : 0x054000  (336 KB)
  VM address  : 0xfffffe00088f0000
  VM size     : 0x054000
```

The segment is **entirely zero** at load time — every byte is 0x00 in the
kernelcache. SPTM fills it in at GL2 during boot, after the `(0x0:0xf)` boot
handoff call. XNU never writes this memory directly; all mutations go through
SPTM calls.

This is where SPTM stores its runtime data structures: memory type maps,
page table roots, trust caches, etc. The shim must leave a valid-looking
`__DATA_SPTM` region at the correct VA for SPTM to populate.

---

## Phase 0 findings summary

| Question                                        | Status  | Answer                                    |
|-------------------------------------------------|---------|-------------------------------------------|
| SPTM calling convention                         | ✓ done  | `mov x16, #(subsys<<32|idx); GENTER` (0x00201420) |
| Boot handoff call number                        | ✓ done  | `(0x0, 0xf)` — single call after VBAR    |
| `__DATA_SPTM` structure                         | ✓ done  | 336KB, all-zero at load, GL2-initialized |
| `currentg` register purpose                     | ✓ done  | GL2 re-entrancy guard; pre-hook spins     |
| Number of subsystems                            | ✓ done  | 9 active (0x0, 0x3, 0x5–0x7, 0x9–0xb, 0xd) |
| SPTM binary accessibility                       | ✓ done  | LZFSE, **unencrypted** ARM64e Mach-O; ver 611.120.6 M4=M5 |
| SPTM GENTER dispatcher located                  | ✓ done  | runtime `gxf_entry_el1 = 0xfffffff0270a454c` (T8132) |
| SPTM dispatch model                             | ✓ done  | **domain state machine** (`dispatch_state_machine` @ `0xec010`); registered tables w/ per-domain permissions; XNU↔SPTM↔TXM |
| `sptm_register_dispatch_table` semantics        | ✗ open  | table_id space, entry format, permission model |
| `(0x0:0xf)` → which state transition            | ✗ open  | enumerate states/events behind the state machine |
| m1n1 shim entry mechanism                       | ◑ draft | patch 0004 — genter stub + (0x0:0xf), unverified |
| What SPTM validates about the boot object       | ✗ open  | Need SPTM binary RE (drives shim pre-state) |
| Minimum call sequence for shim                  | ✗ open  | Likely just `(0x0:0xf)` but unverified   |
| Passthrough mode for non-macOS boot targets     | ✗ open  | Unknown                                   |
| Subsystem semantics for 0x3, 0x5, 0x6, 0x7, 0xd | ✗ open | Need deeper caller tracing               |

---

## Related: M4/M5 CPU features in m1n1

Existing upstream m1n1 scaffolding (from `src/soc.h`, `src/midr.h`,
`src/chickens.c`):
- T8132 (M4 base): `EARLY_UART_BASE 0x3ad200000`; MIDR Donan E=0x52 P=0x53
- `features_m4` struct exists; A18 Pro (T8140 Tahiti, 0x60/0x61) reuses it
- PMU/PMGR: new `group_and_offset` addressing scheme (commit fcaf4765c4)
- SMP start offset defined

### Verified ADT data (macOS 26.5 25F71 device trees)

UART base is the `uart0` node `reg` translated through that board's `arm-io`
`ranges` property — **not** a fixed offset. The `arm-io` parent base differs
per SoC (0x2_00000000 for most; 0x2_10000000 for T8142). Method calibrated
against the known upstream value: J604 (M4 base) `uart0` reg 0x1_ad200000 →
0x3_ad200000 = m1n1 `T8132 EARLY_UART_BASE`. ✓ Values below are consistent
across every board variant of each SoC in the IPSW.

| SoC          | ID    | E-core / P-core ADT compatible | UART base (phys) |
|--------------|-------|--------------------------------|------------------|
| T8132 M4     | t8132 | `apple,sawtooth` / `apple,everest` | `0x3ad200000` |
| T6040 M4 Pro | t6040 | `apple,sawtooth` / `apple,everest` | `0x429200000` |
| T6041 M4 Max | t6041 | `apple,sawtooth` / `apple,everest` | `0x429200000` |
| T8142 M5     | t8142 | `apple,sawtooth` / `apple,everest` | `0x3a5200000` |
| T6050 M5 Pro | t6050 | `apple,sawtooth` / `apple,everest` | `0x505200000` |
| T6051 M5 Max | t6051 | — (absent from 25F71)              | UNKNOWN        |

**Correction to earlier notes (board-ID mix-up):** M4/M5 do *not* use
blizzard/avalanche. Every M3/M4/M5 SoC in this IPSW reports `apple,sawtooth`
(E) / `apple,everest` (P) in its `cpu` node `compatible` — these have been the
Linux-DTS core names since M3 (blizzard/avalanche = M2; icestorm/firestorm =
M1). The earlier "M5 base = blizzard/avalanche" claim came from reading the
wrong boards: `j493ap`/`j504ap`/`j514sap` are M2/M3-generation (Mac14,7 /
Mac15,3 / Mac15,6), not M5. Correct M5 boards: M5 base = J704/J813/J815
(Mac17,2-4); M5 Pro = J716s/J716c/J714s/J714c (Mac17,6-9).

Two distinct namespaces: the ADT `compatible` string (`apple,everest`, used in
the Linux DTS) is **separate** from m1n1's MIDR codename (`Donan`, `Tahiti`).

**T6051 (M5 Max) is entirely absent from 25F71** — no device tree, no
kernelcache. Its UART base, MIDR, and core layout cannot be derived from this
firmware; a later IPSW (or hardware) is required.

Core layout (ADT cpu nodes, max enumeration): M4 base 6×E + 4×P; M4 Pro/Max
4×E + 12×P; M5 base 6×E + 4×P. M5 Pro J716s enumerates 36 cpu nodes (12 P-type
+ a 24-wide `M`-type cluster); the `M` grouping is the ADT's maximal topology
and does not map 1:1 to bring-up cores — needs interpretation. Note m1n1
`MAX_CPUS` is a single global `24` in `smp.h`, so high-core M5 Pro/Max configs
may require bumping it.

MIDR part IDs for M5/M4-Pro/Max remain unknown — `cpu-impl-reg` in the ADT
holds MMIO base addresses, not MIDR values; need `mrs midr_el1` on real
hardware. M4 base is known (Donan 0x52/0x53); since A18 Pro Tahiti already
occupies 0x60/0x61, the M5 part-ID base is genuinely uncertain (not simply
"next 0x5x").

---

## References

- Sven Peter CCC December 2025 talk: https://media.ccc.de/v/39c3-asahi-linux-porting-linux-to-apple-silicon
- Sven Peter Mastodon (M4 "rather painful"): https://social.treehouse.systems/@sven/114278224116678776
- Asahi M4 feature support: https://asahilinux.org/docs/platform/feature-support/m4/
- m1n1 M4 UART commit: `0eeca15359` (2025-10-21)
- m1n1 PMGR M4 Pro/Max commit: `fcaf4765c4` (2026-05-15, Yureka)
- XNU source (kernelcache): `kernelcache.release.Mac16,1_2_3_10_12_13` from
  `UniversalMac_26.5_25F71_Restore.ipsw`
