# m5-linux — Linux on Apple M4 / M5

Reverse-engineering notes, m1n1 scaffolding, and a planning track for running
Linux on Apple Silicon Macs that ship the **Secure Page Table Monitor (SPTM)**
— M4 family, M5 family, and (under current macOS) M2 / M3 family too. Built
on, and intended to feed back into, the [Asahi Linux](https://asahilinux.org/)
project.

> **Status:** the static reverse-engineering picture is closed end-to-end,
> and both code artifacts (m1n1 SPTM-shim patches and the Linux
> `arch/arm64/mm/sptm.c` scaffold) **build clean against current upstream
> trees**. No hardware bring-up yet. All findings are from publicly-
> available IPSW firmware; no Apple binaries are redistributed here.

---

## What this is for

Starting with M4 (and back-deployed to M2/M3 in macOS 26.0+), Apple's SPTM
runs at a privilege level (**GL2**) above EL2 — above the OS, above any
hypervisor. Asahi's existing approach for M1–M3 (boot macOS as a guest under
m1n1, intercept MMIO accesses to reverse-engineer hardware) **does not work
once SPTM is active**: SPTM owns the page tables and won't let a hypervisor
silently observe macOS.

The path forward, proposed publicly by Sven Peter at CCC 2025, is an
**XNU-shim**: a minimal stub that satisfies SPTM's boot protocol without
running real macOS, then transfers control to Linux. This repo works out
what that shim has to do, from static analysis of Apple's firmware.

## The headline findings

All from the 25F71 (macOS 26.5) Universal Mac IPSW, statically — no
hardware runs were performed.

1. **SPTM is extractable.** `Firmware/sptm.<chip>.release.im4p` is
   **LZFSE-compressed and NOT encrypted** — decompresses to a clean ARM64e
   `MH_EXECUTE` Mach-O (~1.2 MB). Source version `611.120.6` is identical
   across M3, M4, and M5; per-chip binaries differ only in chip constants.
2. **iBoot is extractable.** Same story: LZFSE-compressed, unencrypted,
   raw ARM64 (`mBoot-18000.120.36`, ~3 MB).
3. **The boot handoff `(subsys=0, idx=0xf)` is a single `genter`** —
   parameterless from the OS side. It does **not** flip enforcement;
   iBoot's `sptm_bootstrap_late` already set `enforce_after` (bit `0x800`
   in the global stage register at `0xfffffff027104000`) before the OS
   ever ran. The OS-side shim's full SPTM responsibility is that one call.
4. **SPTM has no permanent passthrough / audit-only mode.** Bootstrap is
   monotonic; configuration knobs (`mapping-enforcement-mode`,
   `uat-enforce-gpu-carveout`) tune subsystems, not global enforcement.
5. **No non-XNU supervisor domain exists.** `EXEC_MODE_*` enumerates only
   `SPTM_DEFAULT`, `TXM_DEFAULT`, `XNU_DEFAULT`. Frame types are
   `XNU_*` / `SPTM_*` / `TXM_*`. **Linux must masquerade as XNU.**
6. **iBoot's SPTM setup is gated on the kernel image being a properly-
   shaped Boot Kernel Collection.** iBoot's `load_kernelcache` rejects
   the image (panics) if it isn't `MH_FILESET` with the standard Apple
   segment layout. m1n1 must be packaged as an SPTM-aware BootKC for
   iBoot to register it with SPTM. Permissive boot policy gates *which*
   image iBoot accepts, not the SPTM-init path. The packager is at
   `scripts/pack-bootkc.py`; the full RE writeup is in
   `docs/bootkc-packaging.md`. (Earlier claim of a literal
   `kc_layout->present` field has been downgraded — `__DATA_SPTM` is
   all-zero on disk and runtime-populated.)
7. **Per-SoC activation timeline** (BuildManifest diff across releases):
   M2 had SPTM in its boot chain by 15.0; M4 was born with it (15.2);
   **M3 only got SPTM at macOS 26.0** (none from launch 14.1 through
   15.6). Rollout was not newest-first. M1 base (`t8103`) never got it —
   the platform Asahi's hypervisor was built on.
8. **Complete `(subsys, idx)` enumeration**: 148 unique call numbers
   across 9 subsystems, labeled by XNU caller-function names. See
   `analysis/sptm/sptm-call-numbers.csv` and
   `analysis/sptm/sptm-subsys0-labels.csv`.

## What's in this repo

```
docs/
  architecture.md            — project overview and chip targets
  sptm-research.md           — the full RE writeup (~700 lines)
  linux-mmu-port-scope.md    — scoping for the Linux MMU port to sptm_uat_*

m1n1-patches/
  0001-soc-add-M4-Pro-Max-and-M5-chip-IDs.patch
  0002-midr-add-M4-Pro-Max-and-M5-CPU-part-IDs.patch
  0003-chickens-add-M4-Pro-Max-and-M5-cpu-feature-stubs.patch
  0004-sptm-add-xnu-shim-boot-handoff.patch    — the OS-side shim stub

analysis/
  sptm/                      — extraction script + decoded CSVs (call numbers,
                               labels). Apple firmware payloads gitignored.
  iboot/                     — extraction script. Payloads gitignored.
  manifests/                 — BuildManifest diff tooling. Manifests gitignored.

linux-sptm/                  — source-of-truth for arch/arm64/mm/sptm.c +
                               arch/arm64/include/asm/sptm.h + README. Applied
                               into a kernel tree by scripts/apply-linux-sptm.py.

scripts/
  apply-m1n1-patches.py      — idempotent installer; clone Asahi m1n1, apply
                               our patches 0001-0004 onto src/.
  apply-linux-sptm.py        — idempotent installer; drop linux-sptm/ files
                               into a kernel tree, wire Kconfig + Makefile.

ipsw/                        — IPSW download location (gitignored). Get the
                               UniversalMac_*.ipsw yourself from Apple.

build/                       — gitignored. Local clones of upstream m1n1 +
                               linux-asahi for compile-checking. Rebuild from
                               scratch via the apply scripts above.
```

## What's *not* in this repo

- **No Apple firmware binaries.** SPTM, iBoot, TXM, and kernelcache payloads
  are Apple's, not redistributable. The extraction scripts (`analysis/*/extract-*.sh`)
  re-derive everything from a locally-downloaded IPSW.
- **No working shim.** Patches `0001–0004` apply cleanly and `m1n1.macho`
  builds, but: runtime-correctness is per static analysis, unverified on
  hardware, and contingent on m1n1 being packaged as an SPTM-aware Boot
  Kernel Collection (separate tooling work, not done).
- **No working Linux MMU port.** `linux-sptm/arch/arm64/mm/sptm.c` is a
  compile-clean scaffold; the GENTER stub (`sptm_call`) and boot-handoff
  (`sptm_boot_handoff`) are structurally complete, but most wrapper bodies
  are still TODO (see per-function comments). The port is scoped at
  ~1500–3000 LoC in `docs/linux-mmu-port-scope.md`; the scaffold accounts
  for ~150.

## Reproducing the analysis

Requires `tools/ipsw` (the [blacktop/ipsw](https://github.com/blacktop/ipsw)
CLI) and a downloaded `UniversalMac_*.ipsw` placed in `ipsw/`.

```
analysis/adt/extract-adt.sh              # device trees + per-chip kernelcaches
analysis/sptm/extract-sptm.sh            # SPTM monitor blobs
analysis/iboot/extract-iboot.sh          # iBoot blobs
analysis/manifests/date-sptm-activation.sh
                                         # BuildManifest diff across macOS
                                         # releases (fetches manifests only,
                                         # not full IPSWs)
```

The CSVs in `analysis/sptm/` are checked in — they are derived analysis
artifacts, not redistributed firmware.

## Reliability assessment, in one paragraph

A 100%-reliable shim **per pinned firmware + SoC** is achievable. SPTM does
not attest the boot object — its boot-path crypto is page-table integrity
plus a hibernation-image HMAC (disable hibernation), and code-signing lives in
TXM + AMFI, which Asahi's permissive boot policy already neutralizes. The
shim rides only the stable ABI (`genter` + call numbers). SPTM is per-release
firmware that changes every macOS update (binary churn), but its architecture
is identical across versions and SoCs — so write the shim against the ABI,
validate per pinned firmware, exactly the way Asahi already tracks firmware
today. A single forever-universal binary is *not* achievable, because SPTM
isn't a forever-universal binary either. Full assessment:
[`docs/sptm-research.md`](docs/sptm-research.md).

## Prior art and credit

This work is downstream of, and would not exist without:

- **[Asahi Linux](https://asahilinux.org/)** — the entire methodology, the
  m1n1 bootloader/hypervisor, Apple Silicon DT format work, the firmware
  pinning model, and every Linux driver this would eventually rely on.
- **Sven Peter** — public framing of the XNU-shim approach (CCC 2025) and
  the m1n1 PMGR / chickens scaffolding for M4 Pro/Max that this repo
  extends.
- **[blacktop/ipsw](https://github.com/blacktop/ipsw)** — the IPSW
  extraction tooling used throughout `analysis/`.

If any of this ends up useful upstream, it is intended for upstream.

## License

The patches in `m1n1-patches/` carry the upstream m1n1 SPDX header
(`MIT`) where applicable. Original documentation and analysis scripts in
this repo are MIT unless otherwise marked. Apple firmware referenced by
file path or by reverse-engineered findings remains Apple's; nothing in
this repo redistributes any of it.
