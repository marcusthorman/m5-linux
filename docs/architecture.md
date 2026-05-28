# M4/M5 Linux Bring-Up — Architecture and Approach

## Goal

Arch Linux (ARM) on Apple M5 hardware, with full M4 support as a prerequisite.
Builds on and contributes back to the Asahi Linux project stack.

## The blocking problem: SPTM

The Secure Page Table Monitor (SPTM) runs at GL2 privilege — above the OS. It
breaks the Asahi hypervisor-based reverse-engineering methodology (can no longer
run macOS as a guest to trace MMIO registers). M4 is where this became the
practical blocker, but SPTM is **not** M4-exclusive: under macOS 26.5 it is a
signed boot component for M2 and M3 as well (everything except the original M1
t8103) — see `docs/sptm-research.md`. The same SPTM build (ver 611.120.6) is
used M3→M5, so this work applies beyond M4.

The only known path forward is an **XNU shim**: a minimal stub that satisfies
SPTM's boot protocol without running real macOS, then transfers control to
Linux. This requires understanding SPTM's exact interface.

## RE approach: static analysis of the Universal IPSW

Apple's `UniversalMac_26.5_25F71_Restore.ipsw` (18GB) contains all Apple
Silicon Mac firmware in a single file. Key components to extract:

1. **XNU kernel** — find every `__sptm_*` call site; reconstruct SPTM's
   interface from the caller side. Accessible (compressed Mach-O).
2. **SPTM binary** — understand the boot protocol from the callee side.
   May be encrypted with Apple's GID key; determine accessibility first.
3. **Apple Device Tree (ADT)** — complete hardware map: base addresses,
   interrupt numbers, power domains for T8132 (M4), T6040/T6041 (M4
   Pro/Max), T8142 (M5), T6050/T6051 (M5 Pro/Max). Use to write DTS files.
4. **IOKit kexts** — driver source of truth for peripheral register
   protocols. Disassemble for each peripheral we need to bring up.
5. **RTKit firmware blobs** — AGX (GPU), DCP (display), ANE (neural engine).
   Needed for writing Linux coprocessor drivers.

## Chip targets

| SoC | ID | Devices | Status |
|-----|----|---------|--------|
| M4 base | T8132 | MBP 14" base | m1n1 UART (upstream, 0x3ad200000); all else TBA |
| M4 Pro | T6040 | MBP 14"/16" Pro | UART verified 0x429200000 (patch 0001); PMGR upstream |
| M4 Max | T6041 | MBP 14"/16" Max | UART verified 0x429200000 (patch 0001); PMGR upstream |
| M5 base | T8142 | Mac17,2-4 (J704/J813/J815) | UART verified 0x3a5200000 (patch 0001) |
| M5 Pro | T6050 | Mac17,6-9 (J716/J714 c/s) | UART verified 0x505200000 (patch 0001) |
| M5 Max | T6051 | — | absent from 25F71 IPSW; nothing confirmable |

## Dependency chain

```
SPTM interface mapped (static RE of XNU + SPTM binary)
    → XNU shim implemented in m1n1
        → m1n1 can boot Linux on M4
            → M4 ADT → Linux DTS → peripheral bring-up (NVMe, PCIe, DART, WiFi...)
                → M4 installer-ready (months after m1n1 shim + GPU)
                    → M5 SoC scaffolding in m1n1 (same SPTM shim, new chip IDs)
                        → M5 DTS, peripheral bring-up
```

## Stack (same as Asahi, extended)

| Layer | Component | Our work |
|-------|-----------|----------|
| Boot stage 1 | m1n1 (signed by Apple) | No changes possible |
| Boot stage 2 | m1n1 fork | XNU shim for SPTM; M5 chip IDs; M5 DTS |
| UEFI | U-Boot fork | M4/M5 board support |
| Kernel | linux-asahi fork | M4/M5 DTS; driver work |
| GPU (user) | Mesa (upstream) | Track Asahi GPU work for M4/M5 |
| Base distro | ALARM aarch64 | Packaging + installer |

## Work phases

### Phase 0 — Static RE (no hardware needed)
- Extract and map XNU SPTM call sites
- Determine SPTM binary accessibility
- Extract ADT for all M4/M5 SoCs → write initial DTS stubs
- Survey IOKit kexts for M4/M5-specific peripherals

### Phase 1 — XNU shim (hardware: any M4 Mac)
- Implement SPTM shim in m1n1 based on Phase 0 findings
- Get Linux booting on M4 base (T8132)
- NVMe + serial console as first milestones

### Phase 2 — M4 peripheral bring-up
- DART (IOMMU) — prerequisite for everything else
- PCIe + NVMe
- WiFi (brcmfmac should work; firmware extraction)
- Display (DCP — likely same issue as M3, needs RE)
- USB-C, trackpad/keyboard

### Phase 3 — M5 bring-up
- M5 chip ID scaffolding in m1n1 (T8142, T6050, T6051)
- M5 DTS from ADT analysis
- Verify SPTM shim works on M5 (same architecture as M4 SPTM)
- M5-specific peripheral differences

## Key Asahi contacts

Track their work; contribute patches upstream:
- Sven Peter — m1n1, SPTM research, new SoC bring-up
- Janne Grunau — kernel DTS, upstream submission
- Alyssa Rosenzweig — GPU driver (M3/M4/M5 ISA RE)
