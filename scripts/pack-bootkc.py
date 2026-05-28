#!/usr/bin/env python3
"""Pack m1n1.macho into an SPTM-aware Boot Kernel Collection (BootKC).

iBoot on Apple Silicon Macs running macOS 14.x+ only registers a kernel
image with SPTM if that image is a properly-shaped BootKC (filetype
MH_FILESET with segments named/shaped the way iBoot's `load_kernelcache`
expects). m1n1 already builds as MH_FILESET but uses compact custom
segment names; this script rewrites them to the standard Apple BootKC
names and adds the minimal extra pieces iBoot/SPTM need.

See docs/bootkc-packaging.md for the static-RE findings driving these
choices.

Usage:
    scripts/pack-bootkc.py [build/m1n1/build/m1n1.macho [out.bootkc.macho]]

Defaults to packing build/m1n1/build/m1n1.macho into
build/m1n1/build/m1n1.bootkc.macho.

Status: structural fix only. Cannot be runtime-validated without
Apple-Silicon hardware. See docs/bootkc-packaging.md "Validation path".
"""
import struct
import sys
import uuid
from pathlib import Path

# Mach-O constants
MH_MAGIC_64       = 0xfeedfacf
MH_FILESET        = 12
LC_SEGMENT_64     = 0x19
LC_UUID           = 0x1b
LC_UNIXTHREAD     = 0x5
LC_BUILD_VERSION  = 0x32
LC_DYLD_CHAINED_FIXUPS = 0x80000034
LC_FILESET_ENTRY  = 0x80000035

# Segment-name remap m1n1 → standard BootKC names
# (See docs/bootkc-packaging.md for the rationale.)
SEGMENT_RENAME = {
    b"_HDR":  b"__TEXT",            # 4 KB header window  → standard __TEXT
    b"TEXT":  b"__TEXT_EXEC",       # main code           → __TEXT_EXEC (BootKC-rx)
    b"RODA":  b"__DATA_CONST",      # RO data             → __DATA_CONST (BootKC-ro)
    b"DATA":  b"__DATA",            # RW data             → __DATA (BootKC-rs)
    b"PYLD":  b"__DATA",            # collides; handled specially below
}

# Reserved __DATA_SPTM segment — declared but file-empty (zero-fill).
# SPTM populates this region at runtime during bootstrap. Size taken
# from a real BootKC (T8132 26.5: 0x54000); m1n1 may get away with less
# but err on the side of "what kc reference uses".
DATA_SPTM_VMSIZE = 0x54000


def parse_macho(data: bytes):
    """Return (header_dict, [load_command_dicts])."""
    magic, cputype, cpusubtype, filetype, ncmds, sizeofcmds, flags, _r = \
        struct.unpack_from('<IIIIIIII', data, 0)
    if magic != MH_MAGIC_64:
        raise ValueError(f"not a 64-bit Mach-O (magic={magic:#x})")
    if filetype != MH_FILESET:
        raise ValueError(
            f"input filetype={filetype} (need MH_FILESET={MH_FILESET}); "
            "check m1n1 build configuration")
    header = dict(magic=magic, cputype=cputype, cpusubtype=cpusubtype,
                  filetype=filetype, ncmds=ncmds, sizeofcmds=sizeofcmds,
                  flags=flags)

    cmds = []
    o = 32
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, o)
        body = data[o:o+cmdsize]
        cmds.append(dict(cmd=cmd, cmdsize=cmdsize, body=bytearray(body), off=o))
        o += cmdsize
    return header, cmds


def find_seg(cmds, name: bytes):
    for c in cmds:
        if c['cmd'] != LC_SEGMENT_64:
            continue
        segname = bytes(c['body'][8:24]).rstrip(b'\x00')
        if segname == name:
            return c
    return None


def rename_segments(cmds, mapping):
    """Rewrite segname (16-byte field at offset +8) for each matching segment."""
    seen_dst = {}
    for c in cmds:
        if c['cmd'] != LC_SEGMENT_64:
            continue
        segname = bytes(c['body'][8:24]).rstrip(b'\x00')
        if segname in mapping:
            dst = mapping[segname]
            # Two source segments mapping to the same destination are not
            # allowed (Mach-O segnames are unique per fileset). For now,
            # only rename the first; warn on duplicates.
            if dst in seen_dst:
                # m1n1's PYLD currently collides with DATA→__DATA. Keep
                # PYLD as-is and warn — iBoot doesn't need to load it.
                print(f"  WARN  segment {segname.decode()} collides with "
                      f"already-renamed {dst.decode()}; left unchanged.",
                      file=sys.stderr)
                continue
            new_name = dst + b'\x00' * (16 - len(dst))
            c['body'][8:24] = new_name
            seen_dst[dst] = segname
            print(f"  rename  {segname.decode():<8} → {dst.decode()}")


def build_chained_fixups_blob() -> bytes:
    """Minimal LC_DYLD_CHAINED_FIXUPS payload — header with seg_count=0.

    Format (dyld_chained_fixups_header followed by dyld_chained_starts_in_image):
      header.fixups_version  = 0
      header.starts_offset   = 0x20  (just past 32-byte header)
      header.imports_offset  = 0
      header.symbols_offset  = 0
      header.imports_count   = 0
      header.imports_format  = 1     (DYLD_CHAINED_IMPORT)
      header.symbols_format  = 0
      starts.seg_count       = 0
    """
    header = struct.pack('<IIIIIII',
        0,        # fixups_version
        0x20,     # starts_offset
        0,        # imports_offset
        0,        # symbols_offset
        0,        # imports_count
        1,        # imports_format
        0,        # symbols_format
    )
    header += b'\x00' * (0x20 - len(header))
    starts = struct.pack('<I', 0)  # seg_count = 0
    blob = header + starts
    # 8-byte align
    while len(blob) & 7:
        blob += b'\x00'
    return blob


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else \
          Path(__file__).resolve().parent.parent / "build/m1n1/build/m1n1.macho"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else \
          src.with_suffix('.bootkc.macho')

    if not src.exists():
        sys.exit(f"ERROR: input not found: {src}")

    data = src.read_bytes()
    print(f"in   {src}  ({len(data):,} bytes)")

    header, cmds = parse_macho(data)
    print(f"  header: filetype={header['filetype']} ncmds={header['ncmds']} "
          f"sizeofcmds=0x{header['sizeofcmds']:x}")

    # 1. Rename segments to BootKC-standard names
    rename_segments(cmds, SEGMENT_RENAME)

    # 1b. Zero PYLD's filesize claim — m1n1's payload area is runtime-
    # populated, but the original Mach-O claims a 64 MB file extent.
    # That extent collides with anything we want to append at end-of-file.
    pyld = find_seg(cmds, b'PYLD')
    if pyld is not None:
        # body offsets: vma=24 vmsz=32 fileoff=40 filesize=48
        struct.pack_into('<Q', pyld['body'], 40, 0)  # fileoff = 0
        struct.pack_into('<Q', pyld['body'], 48, 0)  # filesize = 0
        print("  zero    PYLD fileoff/filesize (runtime-populated; was 64 MB claim)")

    # 1c. Set LC_UNIXTHREAD pc to __TEXT_EXEC's start VMA. m1n1's reset
    # vector is the first instruction of TEXT (renamed __TEXT_EXEC).
    text_exec = find_seg(cmds, b'__TEXT_EXEC')
    if text_exec is None:
        sys.exit("ERROR: __TEXT_EXEC segment missing — cannot set entry point")
    entry_vma = struct.unpack_from('<Q', bytes(text_exec['body']), 24)[0]
    for c in cmds:
        if c['cmd'] != LC_UNIXTHREAD:
            continue
        flavor, count = struct.unpack_from('<II', bytes(c['body']), 8)
        if count != 68:
            continue
        # ARM_THREAD_STATE64 layout: x0..x28 (29), fp, lr, sp, pc, cpsr+pad.
        # PC is the 33rd u64 (index 32, byte offset 256 in state).
        # Command body layout: cmd(4) cmdsize(4) flavor(4) count(4) state(272)
        pc_off = 16 + 32*8
        existing_pc = struct.unpack_from('<Q', bytes(c['body']), pc_off)[0]
        if existing_pc != 0:
            print(f"  keep    LC_UNIXTHREAD pc = {existing_pc:#x} (m1n1 set it)")
        else:
            struct.pack_into('<Q', c['body'], pc_off, entry_vma)
            print(f"  set     LC_UNIXTHREAD pc = {entry_vma:#x}")

    # 2. Add LC_UUID if missing
    if not any(c['cmd'] == LC_UUID for c in cmds):
        u = uuid.uuid4().bytes
        body = bytearray(struct.pack('<II', LC_UUID, 24) + u)
        cmds.append(dict(cmd=LC_UUID, cmdsize=24, body=body, off=None))
        print("  add     LC_UUID")

    # 3. Add LC_BUILD_VERSION if missing (platform=0, sdk=0)
    if not any(c['cmd'] == LC_BUILD_VERSION for c in cmds):
        body = bytearray(struct.pack('<IIIIII',
            LC_BUILD_VERSION, 24, 0, 0, 0, 0))
        cmds.append(dict(cmd=LC_BUILD_VERSION, cmdsize=24, body=body, off=None))
        print("  add     LC_BUILD_VERSION")

    # 4. Add __DATA_SPTM placeholder segment (RW, file-empty)
    if find_seg(cmds, b'__DATA_SPTM') is None:
        # Place its VMA right after the last existing segment
        last_vma_end = 0
        for c in cmds:
            if c['cmd'] != LC_SEGMENT_64:
                continue
            vma, vmsz = struct.unpack_from('<QQ', bytes(c['body']), 24)
            last_vma_end = max(last_vma_end, vma + vmsz)
        # Round up to 16 KB
        sptm_vma = (last_vma_end + 0x3fff) & ~0x3fff
        segname = b'__DATA_SPTM' + b'\x00' * (16 - len(b'__DATA_SPTM'))
        body = bytearray(struct.pack('<II16sQQQQIIII',
            LC_SEGMENT_64, 72,
            segname,
            sptm_vma, DATA_SPTM_VMSIZE,
            0, 0,           # fileoff=0, filesize=0 (declared but unbacked)
            3, 3,           # maxprot=rw-, initprot=rw-
            0,              # nsects
            0,              # flags
        ))
        cmds.append(dict(cmd=LC_SEGMENT_64, cmdsize=72, body=body, off=None))
        print(f"  add     __DATA_SPTM segment  vma={sptm_vma:#x}+{DATA_SPTM_VMSIZE:#x}")

    # 5. Add minimal LC_DYLD_CHAINED_FIXUPS (some iBoot validators expect it)
    if not any(c['cmd'] == LC_DYLD_CHAINED_FIXUPS for c in cmds):
        # The blob lives at end-of-file; we'll patch its dataoff after we
        # decide where it goes. For now, record cmd with placeholder.
        body = bytearray(struct.pack('<IIII',
            LC_DYLD_CHAINED_FIXUPS, 16, 0, 0))  # dataoff, datasize patched later
        cmds.append(dict(cmd=LC_DYLD_CHAINED_FIXUPS, cmdsize=16, body=body,
                         off=None, _needs_fixup_blob=True))
        print("  add     LC_DYLD_CHAINED_FIXUPS (placeholder)")

    # --- Reassemble the Mach-O ---
    new_ncmds = len(cmds)
    new_sizeofcmds = sum(c['cmdsize'] for c in cmds)

    # New header
    new_header = struct.pack('<IIIIIIII',
        header['magic'], header['cputype'], header['cpusubtype'],
        header['filetype'], new_ncmds, new_sizeofcmds, header['flags'], 0)

    # Header+cmds size — new commands may have grown past the original 32+sizeofcmds
    new_cmds_blob = b''.join(bytes(c['body']) for c in cmds)
    new_header_end = 32 + new_sizeofcmds

    # The fixup-chains blob (if any) lives at end-of-file
    fixup_blob = build_chained_fixups_blob()

    # Build the output: start with original data, then patch header+cmds.
    # Caveat: if new header is larger than the original, segments may overlap.
    # For m1n1, original header+cmds occupies 32 + 0x... bytes; the first
    # segment file starts at fileoff=0 (the _HDR/__TEXT 4KB window includes
    # the header). New header must still fit within that 4KB window.
    if new_header_end > 0x4000:
        sys.exit(f"ERROR: new header+cmds ({new_header_end} bytes) exceeds "
                 f"the 16 KB __TEXT window m1n1 reserves")

    out = bytearray(data)
    # Zero the old header+cmds area, then write the new one
    for i in range(32, 0x4000):
        out[i] = 0
    # write header
    out[0:32] = new_header
    # Write commands; first need to compute fixup-blob offset if present
    fixup_off = None
    for c in cmds:
        if c.get('_needs_fixup_blob'):
            fixup_off = len(out)
            # Append the blob to the file
            out += fixup_blob
            # Patch the LC dataoff/datasize fields in this command body
            struct.pack_into('<II', c['body'], 8, fixup_off, len(fixup_blob))
    # Now write all commands sequentially
    o = 32
    for c in cmds:
        out[o:o+c['cmdsize']] = c['body']
        o += c['cmdsize']

    dst.write_bytes(out)
    print(f"out  {dst}  ({len(out):,} bytes)")
    print()
    print("WARNING: this is a structural fix only. Hardware validation is "
          "required to confirm iBoot accepts the result and SPTM activates.")
    print("See docs/bootkc-packaging.md.")


if __name__ == "__main__":
    main()
