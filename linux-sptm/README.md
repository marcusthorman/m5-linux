# linux-sptm — Linux MMU-port scaffold for Apple SPTM

Source-of-truth files for the `arch/arm64/mm/sptm.c` wrapper layer that
routes Linux's page-table mutations through Apple's Secure Page Table Monitor.
Applied to a checked-out kernel tree by `scripts/apply-linux-sptm.py`.

**Status:** scaffold. Compiles in isolation against linux-asahi (Linux 7.0).
Most wrappers are stubs marked TODO; the boot-time GENTER stub (`sptm_call`,
`sptm_boot_handoff`) is structurally complete.

## Files

| Source file | Lands in kernel tree as |
|---|---|
| `arch/arm64/include/asm/sptm.h` | `linux-asahi/arch/arm64/include/asm/sptm.h` |
| `arch/arm64/mm/sptm.c`          | `linux-asahi/arch/arm64/mm/sptm.c` |

Plus two small one-line edits (handled by the apply script):

- `arch/arm64/Kconfig` — add `CONFIG_ARM64_APPLE_SPTM`
- `arch/arm64/mm/Makefile` — `obj-$(CONFIG_ARM64_APPLE_SPTM) += sptm.o`

## What's done

- API surface (`sptm.h`): packed call-number macros for all labeled
  subsys-0 ops + all of subsys 0xb (gen3dart/IOMMU), frame-type and
  exec-mode enums, wrapper-function declarations
- `sptm_call()`: raw GENTER inline-asm stub with correct register usage
- `sptm_boot_handoff()`: issues `(0x0:0xf)` at boot
- Wrapper bodies for the **HIGH-confidence** subsys-0 calls (retype,
  switch_root, register_cpu, uat_unmap_table, map_page)

## What's TODO (per-wrapper, marked in source)

- Verify argument layout for each call against XNU's caller-side context
  in `pmap_arm.c` — `analysis/sptm/sptm-subsys0-labels.csv` is the index
- `broadcast_tlbi_*` — exact `(subsys, idx)` not yet pinned
- Frame-type enum **numeric** values (names are confirmed from SPTM
  binary strings; values are not directly exposed in the binary and need
  XNU caller-side extraction)
- Exec-mode enum **numeric** values (same)
- Hibernation, JIT/TPRO user-mode paths — explicitly out of scope for
  initial port

## Out of scope for initial port

- KVM stage-2 (separate `XNU_STAGE2_*` family, large addition)
- Hibernation (`sptm_hib_*` — disable `CONFIG_HIBERNATION`)
- CPU tracing (`sptm_cputrace_*`)

## Applying to a kernel tree

```
scripts/apply-linux-sptm.py [path/to/linux-tree]   # default: build/linux-asahi
```

Idempotent. Adds the two source files, inserts the Kconfig and Makefile
lines, leaves everything else untouched.
