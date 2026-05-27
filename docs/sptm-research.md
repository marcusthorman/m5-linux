# SPTM Research Notes

## What is SPTM

Secure Page Table Monitor. Introduced with M4 (T8132). Runs at GL2 — a
privilege level above EL2 (hypervisor) and EL1 (kernel). Manages ARM64
page tables from above the OS.

Apple's stated purpose: prevent even a compromised kernel or hypervisor from
modifying page table mappings, closing a class of privilege escalation.

## Why it blocks Linux

The Asahi m1n1 hypervisor works by running XNU as a guest under m1n1 at EL2,
then intercepting MMIO accesses to reverse-engineer hardware. SPTM breaks this:

1. **Boot environment incompatibility**: The boot object (what m1n1 creates to
   hand off to Linux) now lands in an environment where SPTM is already active
   at GL2. SPTM expects EL2 + MMU enabled to configure page tables. Standard
   Linux boot protocol expects EL2 + MMU disabled. These are incompatible.

2. **Hypervisor mode broken**: SPTM intercepts XNU's page table operations. m1n1
   cannot transparently observe macOS anymore — SPTM intermediates everything.
   Running macOS as a guest under m1n1 is no longer possible.

## Known workaround strategy: XNU shim

Proposed by Sven Peter (CCC December 2025 talk). No public implementation yet.

The idea: implement a minimal stub in m1n1 that:
1. Enters the EL2+MMU environment SPTM requires
2. Makes the minimum calls to SPTM to satisfy its initialization protocol
3. Gets SPTM into a state where a modified "XNU-like" handoff can transfer
   control to Linux
4. Hands off to the Linux boot entry point

This requires knowing exactly what those minimum calls are — which is what the
static RE work in Phase 0 is for.

## RE targets

### Primary: XNU kernel SPTM call sites

XNU is a Mach-O binary in the IPSW, accessible (signed, not encrypted).
Target: find every `__sptm_*` function call to reconstruct:
- The SPTM call convention (registers, stack layout)
- The sequence of calls during early boot
- What data structures SPTM validates before accepting a boot transition

Commands (once IPSW is extracted):
```bash
# Extract XNU kernel
ipsw extract --kernel UniversalMac_26.5_25F71_Restore.ipsw

# Find SPTM symbols
ipsw macho info kernelcache.release.t8132 --symbols | grep -i sptm
ipsw macho disass kernelcache.release.t8132 --symbol _sptm_retype_params
```

### Secondary: SPTM binary itself

SPTM is a separate firmware component (not part of XNU). It lives in the
restore image. May be encrypted with Apple's GID key (device-specific) or
with a global key. Need to determine which.

If globally encrypted: may be extractable from IPSW with published keys
or via img4tool if keys are known.
If device-encrypted: must reconstruct interface purely from XNU call sites
(harder but doable).

```bash
# List IPSW components and look for SPTM
ipsw info UniversalMac_26.5_25F71_Restore.ipsw
python3 -c "
import pyimg4, zipfile, io
with zipfile.ZipFile('ipsw/UniversalMac_26.5_25F71_Restore.ipsw') as z:
    print([f for f in z.namelist() if 'sptm' in f.lower() or 'TrustCache' in f or 'sep' in f.lower()])
"
```

## Key questions to answer in Phase 0

1. What does `_sptm_retype_params` do? What's the memory type system?
2. What is the sequence of SPTM calls in XNU's `arm_vm_init()`?
3. What does SPTM validate about the "boot object" before accepting handoff?
4. Is there a "raw mode" or "passthrough" for non-macOS boot targets?
5. Does SPTM check the XNU binary signature, or just the boot protocol?
   (If the latter, the shim can be much simpler.)

## Related: M4 CPU features in m1n1

Existing m1n1 scaffolding for M4 (from `src/chickens.c`):
- T8132 UART base defined
- features_m4 struct exists (reused from A18 Pro)
- PMU/PMGR: new `group_and_offset` addressing scheme (commit fcaf4765c4)
- SMP start offset defined

M5 (T8142) has none of this yet. Once M4 SPTM is solved, M5 scaffolding
is straightforward (same architecture, new chip IDs + minor register deltas).

## References

- Sven Peter CCC December 2025 talk: https://media.ccc.de/v/39c3-asahi-linux-porting-linux-to-apple-silicon
- Sven Peter Mastodon (M4 "rather painful"): https://social.treehouse.systems/@sven/114278224116678776
- Asahi M4 feature support: https://asahilinux.org/docs/platform/feature-support/m4/
- m1n1 M4 UART commit: `0eeca15359` (2025-10-21)
- m1n1 PMGR M4 Pro/Max commit: `fcaf4765c4` (2026-05-15, Yureka)
