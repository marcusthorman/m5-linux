# SPTM subsys-0 caller scan (com.apple.kernel, T8132 26.5)

Derived from a full enumeration of GENTER (0x00201420) instructions in
`com.apple.kernel` extracted from the 25F71 Universal IPSW, with each
GENTER's call number recovered from the preceding `mov x16, #imm` /
`movk x16, #imm, lsl #32` pair. The result is a one-to-one stub table:

- 50 subsys-0 stubs (idx 0x00..0x31), each a 5-instruction wrapper at
  fixed 40-byte stride starting at `0xfffffe000c06676c` (T8132 26.5).
- Stubs are stripped of symbols in com.apple.kernel **except**
  `_sptm_retype` (0x01) — confirmed via the symbol annotation in the
  llvm-objdump output.
- Subsystems 3, 5, 6, 7, 9, 0xa, 0xb, 0xd, 0xf each occupy a contiguous
  stub block further on (gen3dart at 0xb, cputrace at 0x7, others
  unannotated).

## High-confidence labels from caller __func__ scan

For each subsys-0 stub, we found all BL call sites in com.apple.kernel
and then walked back from each caller up to ~60 instructions to find the
nearest `adrp+add` reference into __cstring — that target string is
typically the caller's __func__ or panic-format string and pins the
caller's identity.

| idx  | caller __func__ / format hint                       | likely SPTM op                          | confidence | notes                                              |
|------|-----------------------------------------------------|-----------------------------------------|------------|----------------------------------------------------|
| 0x01 | `_sptm_retype` symbol annotation in stub             | `sptm_retype`                           | CONFIRMED  | only subsys-0 stub still carrying its symbol       |
| 0x15 | `pmap %p (pte %p): wired count underflow`            | `sptm_map_page` (set_pte path)          | HIGH       | one caller decoded: x0=[x8,+0xe18], x1=[x19,+0x44] |
| 0x2b | `attempt to remove mappings from commpage pmap`      | `sptm_uat_unmap_table` (pmap_remove)    | HIGH       | matches existing CONFIRMED label                   |
| 0x2c | `sptm_get_table_mapping_count returned failure`      | `sptm_unmap_table` / unmap continue     | HIGH       | matches existing                                   |
| 0x05 | `out of windows`, `range crosses DRAM boundary`      | `sptm_update_region`                    | MED+       | sweeps a phys range                                |
| 0x07 | `failed to map CPU copy-window VA`                   | `sptm_disjoint_op` (CPU copy window)    | MED+       | per-CPU temp-mapping helper                        |
| 0x14 | `VA->PA translation failed for va`                   | translation/probe helper                | MED        | was previously "debug/IRQ helper"                  |
| 0x21 | `invalid VM page allocated for TXM`                  | `sptm_register_xnu_exc_return` (TXM)    | MED+       | TXM-adjacent                                       |
| 0x10 | `sptm_get_table_mapping_count returned failure`      | mapping-count-related (slide?)          | MED        |                                                    |
| 0x0c | `sptm_get_paddr_type returned failure`               | paddr-type query                        | MED        |                                                    |
| 0x09 | `debug exceptions enabled in kernel mode`            | configure_shared_region (debug guard?)  | MED        | weaker — string is generic                         |
| 0x0e | `running`                                            | cputrace start (was confirmed)          | LOW        | string too generic; existing label stands          |
| 0x08 | `invalid PV head ... for PA`                         | pv-list helper                          | MED        |                                                    |
| 0x02 | `out of windows`                                     | (shares pattern with 0x05)              | LOW        | existing MED label `sptm_uat_map_table` stronger   |

## Stubs with zero direct BL callers — even fileset-wide

The kext-inclusive scan against `kernelcache.release.Mac16,1_2_3_10_12_13`
(13 MB `__PRELINK_TEXT` + 55 MB `__TEXT_EXEC`) added exactly **one** new
caller — for idx 0x00. The remaining set still has zero direct BL
callers anywhere in the fileset:

    0x0d, 0x0f, 0x16, 0x17, 0x18, 0x19, 0x1a, 0x1b, 0x1c, 0x1d,
    0x22, 0x23, 0x24, 0x25, 0x26, 0x29, 0x2a, 0x2d, 0x2f, 0x30, 0x31

Interpretation: these stubs are reached only via **indirect dispatch** —
PAC-signed `blraa` through function pointers in `__DATA_CONST` (most
likely a `struct uat_ops`-style vtable). Static BL-search cannot pin
them; recovery needs either:

  1. Decoding the chained-fixup function-pointer table that holds them
     (Apple's arm64e ABI; values in the file are fixup records, not
     real addresses), or
  2. Runtime observation on hardware.

`sptm_uat_init_state` and `sptm_uat_destroy_state` (handler bodies at
SPTM VMA `0xfffffff0270ba008` and `0xfffffff0270b9c18`) fall in this
indirectly-dispatched set; their idx remains unpinned.

Layout note: idx `0x18..0x1c` live OUTSIDE the main stub block — they
are at `0xfffffe000c067cc8..0xfffffe000c067d18` in the fileset, mixed
into the cputrace-stubs region. The other indirectly-dispatched idxs
sit in the main block but have no static caller.

## Chained-fixup decode (round 2 — done properly)

A real decoder for `DYLD_CHAINED_PTR_ARM64E_KERNEL` (pointer format 8,
which is what Apple Silicon kernelcaches use) is now in
`scripts/decode-kc-fixups.py`. It walks every chain in every
fixup-bearing segment and emits one CSV row per resolved slot.

For the 25F71 M4 kernelcache (`Mac16,1_2_3_10_12_13`), the decoder
walked **1,002,606 slots** across `__DATA_CONST` / `__DATA` /
`__DATA_SPTM`. Filtered to the 50 subsys-0 SPTM stubs:

    # decoded 1,002,606 slots; emitted 0

**Zero** vtable references to any subsys-0 stub. Not even
`_sptm_retype` (28 BL callers) is vtable-held — it's BL-only.

Filtered to the broader stub region `0xfffffe000c066000..0xfffffe000c068000`:
13 slots, **all contiguous** at `0xfffffe000819ae98..0xfffffe000819aef8`
in `__DATA_CONST`. Targets are the 13 named cputrace stubs
(`_sptm_cputrace_*`). So one cputrace_ops vtable exists, but it does
not include the 5 unnamed stubs in the adjacent slot block
(`0xc067cc8..0xc067d18`) that sit right next to the cputrace block.

**Conclusion (revised — strong evidence now):**

The subsys-0 SPTM stubs `sptm_uat_init_state` /
`sptm_uat_destroy_state` are NOT reachable from this kernelcache by any
means — neither direct BL (zero callers fileset-wide) nor indirect
dispatch (zero vtable references). The SPTM-side handler bodies exist
(at `0xfffffff0270ba008` and `0xfffffff0270b9c18`), but XNU on M4 does
not invoke them. Plausible interpretations, ordered by likelihood:

  1. **Dead code in this kernelcache.** Apple compiles all 50 subsys-0
     stubs for ABI completeness; only ~30 are actually used on this
     SoC + macOS combo. The init/destroy lifecycle may be handled
     entirely SPTM-side (state objects created lazily on first use,
     destroyed when the owning mm/pmap is torn down via a different
     call), so XNU doesn't need to invoke them explicitly.
  2. **Used by a kext that's loaded later** from
     `/System/Library/Extensions` rather than baked into the boot
     kernelcache (e.g. an external UAT IOKit driver). Not statically
     recoverable from the IPSW.
  3. **Reserved for future-SoC use** (M3 retro-fit, M5/M6 features).

**Implication for Linux port:** the lifecycle TODO in
`linux-sptm/arch/arm64/mm/sptm.c::sptm_uat_init_state` should be
revised — Linux may not need to explicitly issue any
`sptm_uat_init_state` call. The first per-mm UAT operation may simply
work without prior init, with SPTM creating the state lazily. This
will only be confirmable on hardware.

## Method details

```text
1. Walk com.apple.kernel for byte sequence 20 14 20 00 (GENTER LE) on
   4-byte boundaries. → 151 sites total.
2. At each genter@G, decode instruction at G-4: if it is `mov x16, #imm`
   (movz, opc=10), recover the call number; merge in any movk x16 in
   the preceding 4 instructions to get the full 64-bit pack.
3. Stub start = G - 0x14 (5 instructions): pacibsp / stp / mov x29,sp /
   bl icache-block / movz x16 / GENTER / bl icache-block / mov sp,x29 /
   ldp / retab.
4. For each stub address S, walk __text for BL instructions whose
   PC-relative target equals S. → direct callers.
5. For each caller C, walk back up to 60 instructions for the nearest
   adrp+add producing an address in __cstring → likely __func__.
```

Output CSV: `sptm-stub-table.csv` (subsys,idx → stub_addr, GENTER_addr).
