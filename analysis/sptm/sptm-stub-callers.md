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
