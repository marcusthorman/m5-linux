#!/usr/bin/env python3
"""Decode chained fixups in an Apple kernelcache.

Parses LC_DYLD_CHAINED_FIXUPS (pointer_format 8 = DYLD_CHAINED_PTR_ARM64E_KERNEL,
which is what Apple Silicon kernelcaches use), walks every chain in every
fixup-bearing segment, and emits one CSV row per resolved slot:

    slot_vma, slot_file_off, target_vma, is_auth, key, diversity, raw_slot_hex

Useful for finding function-pointer vtables, locating where a known function
address is stored, or sanity-checking which symbols are actually live in the
cache (a slot with no fixup pointing at a stub is strong evidence the stub
is dead code in this kernelcache).

Usage:
    scripts/decode-kc-fixups.py <kernelcache> [--target VMA[,VMA...]] [--out PATH]

  --target VMA       — only emit rows whose target equals one of these VMAs
                       (comma-separated, hex). Default: emit every slot.
  --out PATH         — write CSV here (default: stdout).
"""
import argparse
import csv
import struct
import sys


# --- Mach-O / chained-fixups constants -------------------------------------
LC_SEGMENT_64               = 0x19
LC_DYLD_CHAINED_FIXUPS      = 0x80000034

# Pointer formats we know how to decode.
DYLD_CHAINED_PTR_ARM64E_KERNEL = 8


# --- Pointer-format decoder ------------------------------------------------
def decode_arm64e_kernel(slot: int, cache_base: int):
    """Decode one 8-byte chained-fixup slot.

    Returns (target_vma, is_auth, key, diversity, addrDiv, next_stride, bind_bit).

    For ARM64E_KERNEL the runtime target is `cache_base + target_field`
    (where `cache_base` is the vmaddr of __TEXT in the kernelcache).
    """
    auth = (slot >> 63) & 1
    bind = (slot >> 62) & 1
    next_ = (slot >> 51) & 0x7ff
    if auth:
        target_field = slot & 0xffffffff
        diversity = (slot >> 32) & 0xffff
        addrDiv = (slot >> 48) & 1
        key = (slot >> 49) & 3
    else:
        target_field = slot & 0x7ffffffffff           # bits 0..42
        high8 = (slot >> 43) & 0xff                   # bits 43..50
        target_field |= (high8 << 56)
        diversity = 0
        addrDiv = 0
        key = -1
    return cache_base + target_field, bool(auth), key, diversity, addrDiv, next_, bool(bind)


# --- Mach-O parsing ---------------------------------------------------------
def parse_macho(data: bytes):
    """Return (segments, fixups_dataoff) for an MH_FILESET / Mach-O kernelcache."""
    magic = struct.unpack_from('<I', data, 0)[0]
    if magic != 0xfeedfacf:
        raise SystemExit(f"not a 64-bit Mach-O (magic={magic:#x})")
    ncmds = struct.unpack_from('<I', data, 16)[0]
    o = 32
    segs = []
    fixups_off = None
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from('<II', data, o)
        if cmd == LC_SEGMENT_64:
            segname = data[o+8:o+24].rstrip(b'\x00').decode('utf-8', 'replace')
            vmaddr, vmsize, fileoff, filesize = struct.unpack_from('<QQQQ', data, o+24)
            segs.append(dict(name=segname, vma=vmaddr, vmsize=vmsize,
                             fileoff=fileoff, filesize=filesize))
        elif cmd == LC_DYLD_CHAINED_FIXUPS:
            fixups_off, _ = struct.unpack_from('<II', data, o+8)
        o += cmdsize
    if fixups_off is None:
        raise SystemExit("no LC_DYLD_CHAINED_FIXUPS in this image")
    return segs, fixups_off


def walk_chains(data: bytes, segs, fixups_off: int, cache_base: int):
    """Yield decoded slots from every chained segment.

    Yields tuples: (slot_vma, slot_file_off, target_vma, is_auth, key,
                    diversity, raw_slot, segname).
    """
    starts_offset = struct.unpack_from('<I', data, fixups_off + 4)[0]
    sii_off = fixups_off + starts_offset
    seg_count = struct.unpack_from('<I', data, sii_off)[0]
    seg_info_offsets = [
        struct.unpack_from('<I', data, sii_off + 4 + i*4)[0]
        for i in range(seg_count)
    ]
    fileoff_to_seg = {s['fileoff']: s for s in segs}

    for so in seg_info_offsets:
        if so == 0:
            continue
        sis_off = sii_off + so
        size_, page_size, ptr_fmt = struct.unpack_from('<IHH', data, sis_off)
        seg_offset_field, max_valid_ptr, page_count = \
            struct.unpack_from('<QIH', data, sis_off + 8)
        if ptr_fmt != DYLD_CHAINED_PTR_ARM64E_KERNEL:
            print(f"WARNING: skipping segment {seg_offset_field:#x}: "
                  f"unsupported pointer_format={ptr_fmt}", file=sys.stderr)
            continue
        s = fileoff_to_seg.get(seg_offset_field)
        if not s:
            continue
        for page_idx in range(page_count):
            ps = struct.unpack_from('<H', data, sis_off + 22 + page_idx*2)[0]
            if ps == 0xffff:
                continue
            page_vma  = s['vma']     + page_idx * page_size
            page_file = s['fileoff'] + page_idx * page_size
            slot_pos = ps
            # Per-page chain walk
            while slot_pos < page_size:
                slot_file = page_file + slot_pos
                if slot_file + 8 > len(data):
                    break
                slot = struct.unpack_from('<Q', data, slot_file)[0]
                tgt, auth, key, div, addrDiv, next_, bind_ = \
                    decode_arm64e_kernel(slot, cache_base)
                yield (page_vma + slot_pos, slot_file, tgt, auth, key,
                       div, slot, s['name'])
                if next_ == 0:
                    break
                slot_pos += next_ * 4  # ARM64E_KERNEL stride: 4 bytes


# --- CLI --------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('kernelcache')
    ap.add_argument('--target', default=None,
        help='comma-separated hex VMAs; only emit slots pointing at these')
    ap.add_argument('--out', default=None, help='CSV output path (default stdout)')
    args = ap.parse_args()

    data = open(args.kernelcache, 'rb').read()
    segs, fixups_off = parse_macho(data)
    # Cache base = vmaddr of __TEXT
    text = next((s for s in segs if s['name'] == '__TEXT'), segs[0])
    cache_base = text['vma']
    print(f"# cache_base = {cache_base:#x} (from {text['name']})", file=sys.stderr)

    targets = None
    if args.target:
        targets = {int(t, 0) for t in args.target.split(',')}
        print(f"# filtering to {len(targets)} target VMAs", file=sys.stderr)

    out_f = open(args.out, 'w', newline='') if args.out else sys.stdout
    w = csv.writer(out_f)
    w.writerow(['slot_vma', 'slot_file_off', 'target_vma', 'is_auth',
                'key', 'diversity', 'raw_slot_hex', 'segname'])
    n_emitted = 0
    n_total = 0
    for slot_vma, slot_file, tgt, auth, key, div, raw, segname in \
            walk_chains(data, segs, fixups_off, cache_base):
        n_total += 1
        if targets is not None and tgt not in targets:
            continue
        w.writerow([f'{slot_vma:#x}', f'{slot_file:#x}', f'{tgt:#x}',
                    'Y' if auth else 'n', key, f'{div:#x}',
                    f'{raw:016x}', segname])
        n_emitted += 1
    print(f"# decoded {n_total:,} slots; emitted {n_emitted:,}", file=sys.stderr)
    if args.out:
        out_f.close()


if __name__ == '__main__':
    main()
