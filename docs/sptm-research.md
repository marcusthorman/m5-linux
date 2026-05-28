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

Two caveats: (1) "boot-loaded" is proven from the manifest, but whether SPTM
*enforces* the same hypervisor-breaking restrictions on M2/M3 as on M4 is not
established here. (2) The full **Exclaves/ExclaveOS** secure world is newer:
`Ap,ExclaveOS*` appears as a boot component only for A18 Pro, M5, and M5 Pro —
not M3/M4 — though every SPTM build (incl. M3) contains the XNU↔TXM code.

### Dating SPTM activation on M3 (manifest diff across releases)

SPTM was **not** in M3's boot chain at launch and was added late. Diffing
`Ap,SecurePageTableMonitor` for t8122 (M3) across releases — fetched remotely,
manifest only, via `analysis/manifests/date-sptm-activation.sh`:

| macOS | build   | M3 (t8122) SPTM | note |
|-------|---------|-----------------|------|
| 14.1  | 23B2077 | no  | M3 launch (Oct 2023) |
| 15.0  | 24A335  | no  | Sequoia (M2 t8112 already **yes** here) |
| 15.2  | 24C101  | no  | |
| 15.4  | 24E248  | no  | |
| 15.6  | 24G84   | no  | last Sequoia full IPSW |
| 26.0  | 25A354  | **YES** | **activation point** |
| 26.5  | 25F71   | yes | |

So M3 got SPTM in its boot chain at **macOS 26.0 (25A354)** — not at the M3's
2023 launch. Note the rollout was *not* newest-first: **M2 (t8112) already had
SPTM at 15.0**, a year before M3. This resolves caveat (1) for M3: Asahi's M3
support was built against Sonoma/Sequoia where M3 had **no** SPTM, which is why
the hypervisor RE worked there. On **macOS 26.0+ an M3 now boots with SPTM**, so
the hypervisor method would hit the same wall on M3 as on M4 with current macOS.

**Per-SoC activation (from the same manifest diffs):**
- **M2 (t8112):** SPTM by macOS 15.0 (earliest sampled; retrofitted to a 2022 chip).
- **M4 (t8132):** SPTM from its launch-era OS (present at 15.2, M4's first appearance).
  M4 was *born with* SPTM — there is no pre-SPTM macOS for M4, which is why the
  community experienced this as "the M4 problem."
- **M3 (t8122):** retrofitted only at macOS 26.0 (see table).

Practical upshot: the SPTM protocol RE and the XNU-shim below are the **same
binary/version on M2/M3/M4/M5**, so the work targets all of them — and M3 on
current macOS is now in the same situation as M4, not exempt.

## Reliability assessment: can the shim be 100% reliable?

Short answer: **yes in the only sense that matters here** — deterministic
success on *every* boot for a given pinned firmware + SoC — and **no** in the
sense of "one artifact that works untouched across all future macOS versions."
This matches how Asahi already operates (firmware-pinned, tracked per release),
so it is a maintainable path, not a dead end. Evidence below.

### No fundamental barrier (a non-XNU shim *can* pass)
1. **SPTM does not attest the boot object.** Its boot-path verification is
   page-table integrity (UAT `VIOLATION_*`) plus **hibernation** image HMAC/SHA
   (`sptm_hib_verify_*`, `handoffHMAC`, `image*PagesHMAC`) — and we disable
   hibernation for Linux. Code-signature / trust-cache enforcement lives in
   **TXM** (`TXM_SLAB_CODE_SIGNATURE`, `TXM_SLAB_TRUST_CACHE`) and AMFI, which
   Asahi already neutralizes via the **permissive boot policy** it requires.
   So SPTM will not cryptographically reject a non-Apple handoff target.
2. **The shim only uses the stable ABI** — `genter` + call numbers + the
   dispatch-table registration protocol. It never calls SPTM's internal
   addresses (the RE addresses like `0xa454c`/`0xec010` are for *understanding*,
   not for the shim to branch to). SPTM's resident handler runs at whatever
   address that build placed it.
3. **Determinism:** the handoff is software state transitions. The only per-boot
   inputs (`random-seed`, `cl4-entropy`) are supplied by iBoot via `/chosen` —
   provided to us, not a challenge we must answer.

### What it requires (the pinned-version model)
SPTM is **versioned firmware that changes every release**: t8132 was
`611.0.26` / 1130528 B at 26.0 and `611.120.6` / 1212448 B at 26.5 (+82 KB) —
yet the architecture is *identical* (same `dispatch_state_machine`,
`sptm_register_dispatch_table`, `XNU->TXM`, `Found illegal dispatch entry point`,
same `b → bootstrap` entry; only addresses shift). So the shim is written
against the **ABI**, not addresses, and validated **per pinned firmware** —
exactly Asahi's existing firmware-pinning model. An OS update that bumps SPTM is
handled like any firmware bump: re-pin + re-validate.

### Residual risks (must be validated, mostly on hardware)
- **Multi-die (Pro/Max/Ultra):** per-die SPTM / secondary-CPU GL2 bring-up — the
  M5 Pro binary is larger (multi-die handling). Needs validation on real Pro/Max.
- **Registration must be exactly right:** a wrong dispatch entry point or
  permission set → SPTM rejects (`illegal dispatch entry point`) or panics.
  Deterministic, but unforgiving.
- **ABI drift across *major* macOS versions:** if Apple restructures the
  XNU↔SPTM ABI, the shim needs a per-version update (maintenance, not a wall).
- **Statics can't prove a negative:** there may be a check in the `(0x0:0xf)` /
  registration path that only running on M4/M5 hardware will reveal.
- **M5/A18 Pro ship TXM/ExclaveOS as boot components** (M3/M4 do not) — an extra
  surface to handle on those chips specifically.

### Bottom line
A shim that succeeds 100% of the time on a given pinned firmware + SoC is
achievable and fits Asahi's model. A single forever-universal binary is not,
because SPTM is per-release firmware. The remaining work is: (a) nail the
dispatch-table **registration** protocol, (b) per-SoC / per-die validation on
hardware, (c) track it per firmware — which Asahi tooling already does.

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

### `sptm_register_dispatch_table` — recovered contract

Function entry at `0xfffffff0270ebbb0` (T8132 26.5). Prologue: `pacibsp; sub sp,#0xb0`;
saves x19–x26, sets `x19=arg1`, `x20=arg2`. Body validated against assertion
labels (`table_id`, `entry->dispatch_entry_point`, `permissions`,
`sanitize_integer(dispatch_entry_addr_U.UNSAFE_VALUE)`, `Found illegal dispatch
entry point. caller_domain: %d ...`).

```c
// Reconstructed signature:
int sptm_register_dispatch_table(
    uint8_t   table_id,            // x0 — in [0..15]; checked by validate_sptm_dispatch_table_id
    void     *dispatch_entry_addr, // x1 — PAC-signed entry point (xpaci'd before bounds check)
    uint64_t  permissions          // x2 — packed per-domain `table_permissions`
);
// MUST be called from GL2 (currentg != 0). caller_domain is read implicitly
// from `[tpidr_gl2 + 0xa30]` (the same field the GENTER dispatcher uses); valid
// values 0..4 (5+ panics).
```

Internal state — a clean 2D array `[caller_domain][table_id]`, 16 tables per
domain × 24-byte slots:

```
slot_addr = base[caller_domain<3 ? *dynamic_ptr : static_array]
          + caller_domain * 0x180   //  6 caller_domains × 16 tables × 24 B = 0x900
          + table_id      * 0x18    // 24 B per slot
slot_addr[0]    : existing table pointer — MUST be NULL on first register
                                          (non-NULL ⇒ "Already registered" panic)
slot_addr[0x08]: {dispatch_entry_addr, permissions} pair gets written here
```

The two backing arrays live in SPTM `__DATA` around `0xfffffff027099000` (a
dynamic pointer at +0xbd8 for domains 0–2; a static array at +0x458 for
domains 3–4). 16 tables × 5 domains = 80 registrations total system-wide.

**Entry-point bounds check:** `dispatch_entry_addr` must lie in
`[ caller_domain_RO_base, caller_domain_RO_base + count * 0x4000 )` — the
per-domain RO/text region SPTM tracks (16 KB page granularity), read from a
per-domain state struct (`[x26+0x18]` base, `[x26+0x20]` page count). If outside
the range → `"Found illegal dispatch entry point. caller_domain: %d, entry_point:
%#llx, dispatch_target: %#llx"` and panic.

A sibling `register_sptm_iommu_dispatch_table` (entry near `0xec720`) does the
same shape for the IOMMU dispatch table.

### What this means for the shim

The shim/Linux is treated as a *caller_domain* in SPTM's eyes — the same model
as XNU. To call `sptm_register_dispatch_table` at all you need:

1. A **caller_domain assignment** (from per-cpu GL2 state at `[tpidr_gl2+0xa30]`).
2. A **registered RO range** so SPTM has bounds for the entry-point check.
3. To be **inside GL2** (`currentg != 0`) at call time.

(1) and (2) are properties SPTM sets up at boot, *not* by this function. They
must be established by the **`(0x0:0xf)` boot handoff** (or a sibling boot call)
— that's the call that tells SPTM "here is my RO region; assign me a domain."
Until that runs, register_dispatch_table can't even bind to a sensible slot.

So the contract decomposes into two layers, not one:

- **Layer A — boot handoff (`(0x0:0xf)` + possibly siblings):** establish the
  caller as a domain, declare its RO range, get a caller_domain id assigned to
  this CPU's GL2 state. This is the **un-skippable** part — without it nothing
  else works. **Still open.**
- **Layer B — `sptm_register_dispatch_table`:** install entry-point tables for
  the calls Linux will make. Contract now known (above). Likely **optional**
  for Linux *if* Linux doesn't make further SPTM calls — but that depends on
  whether SPTM enforces UAT on all page-table updates of the caller, which is
  the open question that decides whether a Linux port needs to route every PT
  update through `sptm_uat_*` or can run in a passthrough/relaxed mode.

### Bootstrap stages, enforcement, and the `(0x0:0xf)` route

SPTM bootstrap is a **multi-phase, monotonic** state machine driven by iBoot
before m1n1 ever runs:

```
   sptm_bootstrap_early  ->  sptm_bootstrap_tlbi  ->  sptm_bootstrap_late
                                                            |
                                                            v
                                                  sptm_bootstrap_finalize
```

A **global stage register at `0xfffffff027104000`** tracks progress as a bitmap;
bits are atomically ORed via `ldsetl` and **regression panics**
(`0xfffffff0270bc460`). Stages decoded from strings + xref:

| string                            | bit       | meaning |
|-----------------------------------|-----------|---------|
| `bootstrap_stage_announce`        | (early)   | initial announce phase |
| `bootstrap_stage_enforce_before`  | `0x2000`  | pre-enforcement bootstrap window |
| `bootstrap_stage_enforce_after`   | `0x800`   | full enforcement active |

There is also a state-machine state literally called `STATE_SPTM_BOOTSTRAP`.

The XNU↔SPTM **`(0x0:0xf)` boot handoff** routes as:

```
EL1: mov x16, #0xf; genter
  -> gxf_entry_el1 dispatcher (0xfffffff0270a454c)
  -> type-0 path (esr_gl1 & 0x1f == 0): raw x16 forwarded
  -> 0xfffffff0270ec944 (raw-x16 dispatcher)
     idx != 0x1b/0x1c/0x1e -> w8 = 2 (internal class)
  -> tail-call dispatch_state_machine(class=2, evt=0xf) at 0xfffffff0270ec010
  -> SPTM-internal handler from the static table at 0xfffffff02701a770
```

So `(0x0:0xf)` lands in a **built-in** SPTM handler — the caller does **not**
need to pre-register anything to make this call work. Semantically the call is
the event that drives **`enforce_before -> enforce_after`** for this CPU: "OS
is ready, start enforcing."

### Is there a permissive / passthrough mode? Answer: NO permanent one.

Searched the binary exhaustively for `passthrough`/`audit`/`relaxed`/`permissive`
/`bypass`/`enforce` strings. Findings:

- **No global passthrough/audit-only mode.** The pre-enforcement window
  (`enforce_before`) is real but transient — Apple's iBoot drives SPTM through
  it before handing to the OS, and the stage register is monotonic.
- Configuration knobs exist for **specific subsystems**, not global enforcement:
  - `mapping-enforcement-mode` (a `/chosen`/`/defaults` DT property read in
    `sptm_bootstrap_late`)
  - `uat-enforce-gpu-carveout`, `uat-mapping-limit`
  - DART knobs: `allow-mixed-bypass-mode`, `apf-bypass`, `sptm_init_register_allow_io_range`
- The crypto/verification surface in the boot path is page-table integrity
  (UAT) + **hibernation HMAC** — not boot-object attestation.

### Implication for the shim (refined)

A 100% reliable shim per pinned firmware is still achievable, but the
**shape of the Linux port is bigger** than a single boot stub:

- The shim MUST eventually call `(0x0:0xf)` (or whatever event triggers the
  `enforce_after` transition) — it cannot indefinitely stall in
  `enforce_before` (iBoot has already left SPTM there expecting the OS to
  finish bootstrap promptly).
- Once `enforce_after` is set, **SPTM enforces UAT on the running OS**. That
  means Linux either:
  1. **Ports its PT-update path to call `sptm_uat_*`** (substantial kernel
     work, but tractable — XNU does this);
  2. **Runs as a `caller_domain` whose permissions allow self-managed PT**
     within a declared RO range (worth investigating: the per-domain
     `table_permissions` model in `sptm_register_dispatch_table` hints this
     is possible — needs RE of how XNU's permissions differ from a "guest");
  3. **Stays inside `enforce_before` somehow** — unlikely to be sanctioned,
     but worth checking whether a debug/early-handoff path skips the
     transition.

The next decisive RE target is now option (2): what `permissions` values exist,
and is there a permission set that grants a caller_domain "manage your own PT
range" without per-page SPTM calls? That answer determines whether Linux needs
an MMU port or just a richer shim.

### Permission model — answer: there is no non-XNU self-managed PT mode

Searched the binary for the domain/exec-mode taxonomy. **`EXEC_MODE_*`
enumerates the complete set of supervisor domains:**

```
EXEC_MODE_SPTM_DEFAULT          # SPTM itself (GL2)
EXEC_MODE_TXM_DEFAULT           # TXM (Trusted Execution Monitor)
EXEC_MODE_XNU_DEFAULT           # XNU (the OS) — the only "OS-kernel" mode
EXEC_MODE_XNU_ROZONE_RW         # XNU transient RW into the rozone
EXEC_MODE_XNU_USER_DEFAULT
EXEC_MODE_XNU_USER_JIT_RW
EXEC_MODE_XNU_USER_TPRO_RW
```

There is **no `EXEC_MODE_LINUX_*`, no `_GUEST_*`, no generic third-party OS mode**.
The frame-type taxonomy is the same story: every page-frame class is `XNU_*`,
`SPTM_*`, or `TXM_*`. Sample of the XNU frame types SPTM enforces directly:

```
XNU_DEFAULT, XNU_RO, XNU_ROZONE, XNU_KERNEL_RESTRICTED,
XNU_PAGE_TABLE, XNU_PAGE_TABLE_COMMPAGE, XNU_PAGE_TABLE_SHARED,
XNU_PAGE_TABLE_ROZONE, XNU_STAGE2_PAGE_TABLE, XNU_STAGE2_ROOT_TABLE,
XNU_SHARED_ROOT_TABLE, XNU_USER_ROOT_TABLE, XNU_SUBPAGE_USER_ROOT_TABLES,
XNU_USER_EXEC, XNU_USER_JIT, XNU_USER_DEBUG, XNU_USER_TPRO,
XNU_COMMPAGE_RO/RW/RX, XNU_IO, XNU_IOMMU, XNU_PROTECTED_IO,
XNU_RESTRICTED_IO, XNU_TAG_STORAGE, XNU_COPROCESSOR_RO_IO,
XNU_CPUTRACE_PA_BUFFER, XNU_CPUTRACE_VA_BUFFER, XNU_RO_DBG_RW
```

Retype handlers are XNU-specific too: `xnu_exec_retype_out`,
`xnu_iommu_retype_out`, `xnu_generic_retype_out`,
`xnu_subpage_user_root_tables_retype_out`, `xnu_rozone_retype_out`,
`xnu_tag_storage_retype_{in,out}`.

`VIOLATION_FRAME_OWNER` does exist (every frame has an `owner_domain`, and
`current_type_params->owner_domain` is asserted) — **but** ownership is checked
*inside* the SPTM API, not as a "calling_domain == owner_domain → bypass the
call" shortcut. SPTM's purpose is to *be* the gate; same-domain operations
still go through `sptm_uat_*`.

### Net result: Linux must masquerade as XNU

The XNU-shim path is now fully scoped. Linux on M4+/M3-on-26 needs:

1. **Boot shim (in m1n1)** — small. Drive SPTM through whatever sibling boot
   calls are required, declare Linux's RO/text range, and issue `(0x0:0xf)` to
   trigger `enforce_after`. Patch 0004 has the entry mechanism.
2. **Linux MMU port to the SPTM UAT API** — substantial but bounded. Every
   PT-update path (`set_pte`, `pmd_populate`, TLB invalidate, …) routes through
   `sptm_uat_map_table` / `sptm_retype` / `sptm_uat_unmap_table` / etc. Linux
   self-identifies as `EXEC_MODE_XNU_DEFAULT` and uses `XNU_*` frame types
   for its pages. This is the same architectural pattern Apple did when
   transitioning XNU itself from a direct-pmap model to the SPTM-mediated
   model — work envelope is well-defined and concrete.
3. **Domain assignment + RO-range declaration** — handled at boot by the
   sibling call(s) we still need to RE (the immediate next target, smaller
   scope than the MMU port).

100% reliability per pinned firmware is still on the table — there's nothing
non-deterministic. The cost is the MMU port, not a reliability gap.

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
| `sptm_register_dispatch_table` semantics        | ✓ done  | `(table_id<16, entry_addr, perms)`; per-CPU caller_domain∈0..4; 2D slot array; RO-range bounds check |
| `(0x0:0xf)` route                                | ✓ done  | type-0 raw-x16 dispatcher → `dispatch_state_machine(class=2, evt=0xf)`; built-in handler |
| SPTM bootstrap stages                            | ✓ done  | early/tlbi/late/finalize; stage reg @ `0xfffffff027104000` (bits announce/`0x2000` before/`0x800` after); monotonic |
| Global passthrough/audit-only mode               | ✓ done  | **does NOT exist**; only per-subsystem knobs (`mapping-enforcement-mode` etc.) |
| `uat_bootstrap_parse_dt` /chosen properties      | ◑ part  | known names: `mapping-enforcement-mode`, `pmap-max-asids`, `uat-enforce-gpu-carveout`, `uat-mapping-limit`; values TBD |
| Per-domain `permissions` semantics — does any caller_domain get "self-managed PT"? | ✓ done | **NO.** Only 3 supervisor exec modes (SPTM/TXM/XNU); frame types entirely XNU_*/SPTM_*/TXM_*; ownership enforced inside SPTM API. Linux must masquerade as XNU and port MMU to `sptm_uat_*`. |
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
