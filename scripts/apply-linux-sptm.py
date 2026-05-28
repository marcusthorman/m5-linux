#!/usr/bin/env python3
"""Apply m5-linux SPTM scaffold to a checked-out Linux source tree.

Idempotent. Adds:
  - arch/arm64/include/asm/sptm.h
  - arch/arm64/mm/sptm.c
  - arch/arm64/Kconfig: CONFIG_ARM64_APPLE_SPTM entry
  - arch/arm64/mm/Makefile: obj-$(CONFIG_ARM64_APPLE_SPTM) += sptm.o

Usage:  scripts/apply-linux-sptm.py [path/to/linux]   (default: build/linux-asahi)
"""
import sys, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC  = ROOT / "linux-sptm"
LIN  = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "linux-asahi"

if not (LIN / "arch" / "arm64" / "mm" / "Makefile").exists():
    sys.exit(f"ERROR: Linux tree not found at {LIN}")

MARKER = "# m5-linux: SPTM"

# Copy the two source files
for relpath in [
    "arch/arm64/include/asm/sptm.h",
    "arch/arm64/mm/sptm.c",
]:
    src = SRC / relpath
    dst = LIN / relpath
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists() and dst.read_bytes() == src.read_bytes():
        print(f"unchanged: {relpath}")
    else:
        shutil.copy2(src, dst)
        print(f"installed: {relpath}")

# Insert CONFIG_ARM64_APPLE_SPTM into arch/arm64/Kconfig
# Land it near the ARMv9.4 architectural features menu so it sits with
# other Apple-Silicon-platform-y options.
kconfig = LIN / "arch" / "arm64" / "Kconfig"
text = kconfig.read_text()
if "ARM64_APPLE_SPTM" not in text:
    entry = """

# m5-linux: SPTM
config ARM64_APPLE_SPTM
\tbool "Apple Secure Page Table Monitor (SPTM) integration"
\tdepends on ARCH_APPLE
\thelp
\t  Route page-table mutations through Apple's Secure Page Table Monitor
\t  (SPTM), required for booting Linux on Apple Silicon Macs running
\t  macOS 14.x and newer where SPTM is a signed boot component.
\t  Affects M2, M3, M4, M5 family chips; M1 base (t8103) does not have
\t  SPTM and should leave this disabled.
\t
\t  See docs/sptm-research.md and docs/linux-mmu-port-scope.md in the
\t  m5-linux repo.
\t
\t  Status: scaffold. Most wrappers are stubs; do not enable on hardware
\t  yet without reading the per-call TODOs in arch/arm64/mm/sptm.c.
\t
\t  If unsure, say N.
"""
    # Insert at the end of the file (Kconfig accepts toplevel configs anywhere)
    if not text.endswith("\n"):
        text += "\n"
    kconfig.write_text(text + entry)
    print("installed: CONFIG_ARM64_APPLE_SPTM in arch/arm64/Kconfig")
else:
    print("unchanged: CONFIG_ARM64_APPLE_SPTM already in arch/arm64/Kconfig")

# Add sptm.o to arch/arm64/mm/Makefile
makefile = LIN / "arch" / "arm64" / "mm" / "Makefile"
text = makefile.read_text()
if "ARM64_APPLE_SPTM" not in text:
    # Add after CONFIG_ARM64_MTE line
    needle = "obj-$(CONFIG_ARM64_MTE)\t\t+= mteswap.o\n"
    if needle in text:
        text = text.replace(needle,
                            needle + "obj-$(CONFIG_ARM64_APPLE_SPTM)\t+= sptm.o\n")
        makefile.write_text(text)
        print("installed: obj-$(CONFIG_ARM64_APPLE_SPTM) in arch/arm64/mm/Makefile")
    else:
        print("WARNING: Makefile anchor not found; manual wiring needed")
else:
    print("unchanged: sptm.o already in arch/arm64/mm/Makefile")

print("\nDone. To compile-check (kernel build env required):")
print(f"  cd {LIN}")
print("  make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- defconfig")
print("  echo 'CONFIG_ARM64_APPLE_SPTM=y' >> .config")
print("  make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- olddefconfig")
print("  make ARCH=arm64 CROSS_COMPILE=aarch64-linux-gnu- arch/arm64/mm/sptm.o")
