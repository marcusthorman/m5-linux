# Packaging m1n1 as an SPTM-aware Boot Kernel Collection (BootKC)

This document captures the static-RE picture of what iBoot and SPTM expect
in a BootKC, and the gap between that and what m1n1 builds today. The
companion script is `tools/pack-bootkc.py`.

## What a real Apple BootKC looks like

A kernelcache from a current Apple Silicon IPSW (e.g.
`kernelcache.release.Mac16,1_2_3_10_12_13` in the 25F71 Universal IPSW)
is itself a BootKC. Its high-level shape:

| Mach-O field | Value | Notes |
|---|---|---|
| `magic` | `MH_MAGIC_64` (`0xfeedfacf`) | |
| `cputype` | `CPU_TYPE_ARM64` (`0x100000c`) | `0x1000000` PTR_AUTH bit OR `0xc` arm64 |
| `cpusubtype` | `2` | `CPU_SUBTYPE_ARM64_E` (PAC) |
| `filetype` | `MH_FILESET = 12` | **Required** — iBoot rejects standalone Mach-O |
| `flags` | `0` | no MH_DYLDLINK / MH_PIE here |

Load commands (in order):

1. `LC_UUID` — random 16-byte UUID
2. `LC_BUILD_VERSION` — platform/sdk metadata; zeros are accepted
3. `LC_UNIXTHREAD` — ARM_THREAD_STATE64 with `pc = entry-point VMA`
4. `LC_DYLD_CHAINED_FIXUPS` — points at a fixup-chain blob; even a
   minimal blob (`seg_count=0`) seems to suffice for "no fixups"
5. `LC_SEGMENT_64` × N — the loadable segments (see below)
6. `LC_FILESET_ENTRY` (`cmd = 0x80000035` = `0x35 | LC_REQ_DYLD`) × M —
   one per embedded "file" (kernel + each kext)

Segments observed in a real BootKC (VMAs T8132 26.5):

| segname | initprot | role |
|---|---|---|
| `__TEXT` | r-- | small header window (32 KB) |
| `__PRELINK_TEXT` | r-- | prelinked kext text (~13 MB) |
| `__DATA_CONST` | r-- | RO data (~12 MB) — SPTM's `BootKC-ro` role |
| `__DATA_SPTM` | r-- | **SPTM-owned region** (~344 KB) — runtime-populated |
| `__TEXT_EXEC` | r-x | main code (~55 MB) — SPTM's `BootKC-rx` role |
| `__TEXT_BOOT_EXEC` | r-x | boot entry (~32 KB) — SPTM's `BootKC-bx` role; `LC_UNIXTHREAD.pc` lands here |
| `__PRELINK_INFO` | rw- | kext metadata as XML plist (~4 MB) |
| `__DATA` | rw- | RW data — SPTM's `BootKC-rs` role |
| `__LINKEDIT` | r-- | symbol/string tables (~26 MB) — SPTM's `BootKC-le` role |

## What iBoot and SPTM look for

### iBoot

From string xrefs in iBoot (j604 release, 25F71):

- Validation entry point: `load_kernelcache` at file offset `0x29c38`,
  emits `"Kernelcache too large"` / `"Kernelcache image not valid"`
  on failure.
- The deep validator is `BL 0xcc0c4` from `load_kernelcache+0x148`. It
  walks the Mach-O at the kernel image pointer and returns 0 on success.
- Layout phase: `lay_out_opaque_kernelcache` — copies each segment from
  source to its target physical location and applies fixups in two
  passes ("1st source/dest", "2nd source/dest").
- Segment-by-role strings: `BootKC-ro`, `BootKC-rs`, `BootKC-rx`,
  `BootKC-bx`. These are the *internal* labels iBoot uses; the on-disk
  segment names in real BootKCs are the standard Apple names (`__TEXT`,
  `__TEXT_EXEC`, `__TEXT_BOOT_EXEC`, `__DATA_CONST`, `__DATA`). iBoot
  maps standard names → role-labels by inspecting `initprot` and the
  `__TEXT_BOOT_EXEC` / `__DATA_CONST` / etc. name conventions.

### SPTM

From string xrefs in `sptm.t8132.release.payload`:

- SPTM expects the BootKC segments by role-name. It scans the
  registered kernel image for: `BootKC-ro`, `BootKC-rs`, `BootKC-rx`,
  `BootKC-bx`, `BootKC-rw`, `BootKC-le`, `BootKC-entry`, `BootKC-virt`.
- Bound check it enforces: `"BootKC rs region does not fit xnu_ro_data
  (needed: %zu, have: %zu)"` — SPTM requires the BootKC's `rs` region
  to be large enough for what it expects to put there.
- Range check: `"<x> not fully within BootKC ro region"` — SPTM
  validates that certain RO data lies inside the BootKC-ro segment.

Net: SPTM appears to consume segments under their **BootKC-** logical
names. The on-disk segment names may need to either be those names
directly, or there must exist a mapping table somewhere (e.g. in
`__DATA_SPTM`) that iBoot fills in and hands to SPTM.

## The `kc_layout->present` panic claim

Earlier RE notes (in `docs/sptm-research.md`) cite a panic at iBoot
file offset `0x1223a4` triggered when `kc_layout->present != 1`. The
current re-dump at that offset shows a `mov w0, #0x4c49; bl 0xe2c`
sequence — likely a panic/trace call with code `0x4c49`, but the
*exact* check it backs is not pinned in this round of RE. It is **not**
a check on a discrete struct field in `__DATA_SPTM` — that segment is
all-zero in the file image and is populated at runtime. The "present"
field is most likely a derived bit iBoot sets after successfully
parsing the BootKC's segment table; the panic fires if iBoot's
parser can't locate the segment roles it requires.

Treat the panic-at-`0x1223a4` claim as "iBoot rejects malformed BootKCs
loudly" rather than "there is a literal struct field `present`."

## Where m1n1 stands today

`build/m1n1/build/m1n1.macho` (built from the M4/M5 patch series in
`m1n1-patches/`):

| Field | Value |
|---|---|
| `filetype` | `MH_FILESET = 12` ✅ |
| `cputype` | `0x100000c` (arm64e) ✅ |
| `cpusubtype` | `2` ✅ |
| `LC_UNIXTHREAD pc` | `0x0` ⚠️ — set by reset vector at runtime |
| Segments | `_HDR`, `TEXT`, `RODA`, `DATA`, `PYLD` ❌ — non-standard names |
| `LC_DYLD_CHAINED_FIXUPS` | absent ⚠️ |
| `LC_FILESET_ENTRY` | none ❌ |
| `__DATA_SPTM` | none ❌ |

So m1n1 is already filetype-correct. The gap to "iBoot-acceptable
BootKC" is:

1. **Segment names** — rename or alias to standard Apple names so iBoot
   recognizes them by role: `TEXT` → `__TEXT_EXEC`, `RODA` →
   `__DATA_CONST`, `DATA` → `__DATA`, plus a small `__TEXT_BOOT_EXEC`
   that contains (or trampolines to) the m1n1 reset vector.
2. **`__DATA_SPTM` segment** — declare a reserved RW window (size TBD;
   real kernelcaches use 344 KB; m1n1 might get away with much less).
   SPTM will write into it during boot.
3. **`LC_UNIXTHREAD.pc`** — set to the VMA of m1n1's `_start`. With our
   patches, m1n1's `_start` is in segment `TEXT` at offset 0 → VMA
   `0xfffffe0007008000` (T8132). The `__TEXT_BOOT_EXEC` segment should
   either *be* that, or contain a small trampoline that branches to it.
4. **At least one `LC_FILESET_ENTRY`** — pointing at "m1n1" itself, so
   the fileset is non-empty.
5. **Optional `LC_DYLD_CHAINED_FIXUPS`** — m1n1 doesn't need fixups
   (it's position-fixed), but iBoot's validator may demand the load
   command exist. An empty fixup blob (`seg_count=0` plus a 32-byte
   header) is the safe default.

## What's NOT in this packager

- **No code signing.** Apple's permissive boot policy (already required
  by Asahi) accepts unsigned images; the BootKC packaging here adds
  *layout* compliance, not signature.
- **No XNU stub kexts.** A real BootKC bundles all kexts; m1n1 doesn't
  need them, so we ship a fileset with just one entry (m1n1 itself).
  SPTM may complain that it can't find an XNU image — that's a
  hardware-test discovery, not something static RE can answer.
- **No chained fixups.** m1n1 is statically linked; we emit an empty
  `LC_DYLD_CHAINED_FIXUPS` blob to satisfy any parser that demands it.

## Validation path (hardware-only)

This packaging is a structural fix; it cannot be validated without:

1. Booting iBoot on real Apple Silicon hardware.
2. Pointing iBoot at the packed `m1n1.bootkc.macho` as the kernel image.
3. Observing whether iBoot accepts it (no "Kernelcache image not valid"
   panic) and whether SPTM activates (the BootKC-rs/-ro region checks).

Static-RE confidence: **HIGH** that the structural format is right;
**MEDIUM** that no additional segment or load command is required.
Per-iBoot-version drift is plausible.
