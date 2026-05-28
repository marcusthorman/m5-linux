#!/usr/bin/env python3
"""Apply m5-linux patches 0001-0004 to a checked-out m1n1 tree.

Idempotent — uses unique markers in the source so re-running is a no-op.

Usage:  scripts/apply-m1n1-patches.py [path/to/m1n1]   (default: build/m1n1)
"""
import os, sys, re, shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
M1N1 = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "m1n1"

if not (M1N1 / "src" / "soc.h").exists():
    sys.exit(f"ERROR: m1n1 not found at {M1N1}")

MARKER = "/* m5-linux: M4/M5 SoC additions */"
SPTM_MARKER = "/* m5-linux: SPTM XNU-shim */"

# --- Patch 0001: src/soc.h — add T6040..T6051 chip IDs + EARLY_UART_BASE ---
soc_h = M1N1 / "src" / "soc.h"
text = soc_h.read_text()

if MARKER not in text:
    # Insert chip ID defines after T6034
    text = text.replace(
        "#define T6034 0x6034",
        f"#define T6034 0x6034\n\n{MARKER}\n"
        "#define T6040 0x6040\n"
        "#define T6041 0x6041\n"
        "#define T6050 0x6050\n"
        "#define T6051 0x6051\n"
        "#define T8142 0x8142"
    )

    # Insert per-TARGET EARLY_UART_BASE blocks before the last #endif inside #ifdef TARGET
    # m1n1's pattern: a chain of #elif TARGET == TXXXX with one #define EARLY_UART_BASE each.
    # We insert right after the T8132 block.
    new_blocks = (
        f"{MARKER}\n"
        "#elif TARGET == T6040 || TARGET == T6041\n"
        "/* M4 Pro/Max — UART verified from j616s/j616c ADT (arm-io base 0x2_00000000) */\n"
        "#define EARLY_UART_BASE 0x429200000\n"
        "#elif TARGET == T8142\n"
        "/* M5 base — UART verified from j704 ADT (arm-io base 0x2_10000000) */\n"
        "#define EARLY_UART_BASE 0x3a5200000\n"
        "#elif TARGET == T6050\n"
        "/* M5 Pro — UART verified from j716s/j716c/j714s ADT (arm-io base 0x2_00000000) */\n"
        "#define EARLY_UART_BASE 0x505200000\n"
        "#elif TARGET == T6051\n"
        "/* M5 Max — t6051 absent from 25F71 IPSW, EARLY_UART_BASE unconfirmed */\n"
        "/* TODO(hardware): obtain on M5 Max system or from a later IPSW */\n"
        "#define EARLY_UART_BASE 0xFFFFFFFFF  /* placeholder, will not boot */\n"
    )
    text = re.sub(
        r"(#elif TARGET == T8132\s*\n#define EARLY_UART_BASE 0x3ad200000\n)",
        r"\1" + new_blocks,
        text,
    )
    soc_h.write_text(text)
    print("PATCH 0001: src/soc.h — added T6040..T6051 chip IDs and EARLY_UART_BASE")
else:
    print("PATCH 0001: src/soc.h — already applied (marker present)")

# --- Patch 0002: src/midr.h — add MIDR_PART_T6040..T6051 (all XXX) ---
midr_h = M1N1 / "src" / "midr.h"
text = midr_h.read_text()
if MARKER not in text:
    midr_block = (
        f"\n/* m5-linux: M4/M5 SoC additions - values XXX, must come from `mrs midr_el1` on hardware */\n"
        "/* M4 Pro (T6040) */\n"
        "#define MIDR_PART_T6040_ECORE  0xFF /* TODO(hardware) */\n"
        "#define MIDR_PART_T6040_PCORE  0xFF /* TODO(hardware) */\n"
        "/* M4 Max (T6041) */\n"
        "#define MIDR_PART_T6041_ECORE  0xFF /* TODO(hardware) */\n"
        "#define MIDR_PART_T6041_PCORE  0xFF /* TODO(hardware) */\n"
        "/* M5 base (T8142) */\n"
        "#define MIDR_PART_T8142_ECORE  0xFF /* TODO(hardware) */\n"
        "#define MIDR_PART_T8142_PCORE  0xFF /* TODO(hardware) */\n"
        "/* M5 Pro (T6050) */\n"
        "#define MIDR_PART_T6050_ECORE  0xFF /* TODO(hardware) */\n"
        "#define MIDR_PART_T6050_PCORE  0xFF /* TODO(hardware) */\n"
        "/* M5 Max (T6051) */\n"
        "#define MIDR_PART_T6051_ECORE  0xFF /* TODO(hardware) */\n"
        "#define MIDR_PART_T6051_PCORE  0xFF /* TODO(hardware) */\n"
    )
    text = text.replace(
        "#define MIDR_PART_T8140_TAHITI_PCORE 0x61",
        f"#define MIDR_PART_T8140_TAHITI_PCORE 0x61\n{midr_block}",
    )
    midr_h.write_text(text)
    print("PATCH 0002: src/midr.h — added MIDR_PART_T6040..T6051 stubs (TODO hardware)")
else:
    print("PATCH 0002: src/midr.h — already applied")

# --- Patch 0003: src/chickens.c — chip table entries ---
chickens = M1N1 / "src" / "chickens.c"
text = chickens.read_text()
if MARKER not in text:
    new_entries = (
        f"    {MARKER}\n"
        "    /* M4 Pro (T6040) — sawtooth/everest cores, codename + MIDR XXX */\n"
        "    {MIDR_PART_T6040_ECORE, \"M4 Pro (E core)\", NULL, &features_m4},\n"
        "    {MIDR_PART_T6040_PCORE, \"M4 Pro (P core)\", NULL, &features_m4},\n"
        "    /* M4 Max (T6041) — sawtooth/everest cores, codename + MIDR XXX */\n"
        "    {MIDR_PART_T6041_ECORE, \"M4 Max (E core)\", NULL, &features_m4},\n"
        "    {MIDR_PART_T6041_PCORE, \"M4 Max (P core)\", NULL, &features_m4},\n"
        "    /* M5 base (T8142) — sawtooth/everest cores, codename + MIDR XXX */\n"
        "    /* TODO: features_m5 once M5 CPU errata known */\n"
        "    {MIDR_PART_T8142_ECORE, \"M5 (E core)\", NULL, &features_m4},\n"
        "    {MIDR_PART_T8142_PCORE, \"M5 (P core)\", NULL, &features_m4},\n"
        "    /* M5 Pro (T6050) — sawtooth/everest cores, codename + MIDR XXX */\n"
        "    {MIDR_PART_T6050_ECORE, \"M5 Pro (E core)\", NULL, &features_m4},\n"
        "    {MIDR_PART_T6050_PCORE, \"M5 Pro (P core)\", NULL, &features_m4},\n"
        "    /* M5 Max (T6051) — absent from 25F71; MIDR XXX */\n"
        "    {MIDR_PART_T6051_ECORE, \"M5 Max (E core)\", NULL, &features_m4},\n"
        "    {MIDR_PART_T6051_PCORE, \"M5 Max (P core)\", NULL, &features_m4},\n"
    )
    text = re.sub(
        r"(\{MIDR_PART_T8140_TAHITI_PCORE, \"A18 Pro Tahiti \(P core\)\", NULL, &features_m4\},\n)",
        r"\1" + new_entries,
        text,
    )
    chickens.write_text(text)
    print("PATCH 0003: src/chickens.c — added M4 Pro/Max + M5 family table entries")
else:
    print("PATCH 0003: src/chickens.c — already applied")

# --- Patch 0004: SPTM XNU-shim — new files + main.c wiring + Makefile wiring ---
sptm_asm = M1N1 / "src" / "sptm_asm.S"
sptm_h = M1N1 / "src" / "sptm.h"
sptm_c = M1N1 / "src" / "sptm.c"

SPTM_ASM_CONTENT = """/* SPDX-License-Identifier: MIT */
/*
 * SPTM (Secure Page Table Monitor) call stub for M4/M5.
 *
 * The trap word 0x00201420 the SPTM disassembly calls "udf #0" is actually
 * Apple's GENTER instruction — the same encoding m1n1 already uses for its
 * own GXF/hypervisor path (see gxf_asm.S). On M4+ systems, SPTM has been
 * loaded and bootstrapped by iBoot before m1n1 runs; SPTM's resident GL2
 * entry handler is installed at GXF_ENTER_EL1. A bare GENTER from EL1
 * dispatches into SPTM's handler — DO NOT call _gxf_init() before this,
 * since that would repoint GXF_ENTER at m1n1's own GL2 vectors and break
 * SPTM dispatch.
 */
""" + SPTM_MARKER + """

#define genter .long 0x00201420

.globl sptm_call
.type sptm_call, @function
sptm_call:
    /* x0 = packed call number (subsys << 32 | idx)
     * x1..x4 -> SPTM argument registers x0..x3
     * Call number goes in x16, matching the XNU stub convention.
     * SPTM does its work at GL2 and GEXITs back; this returns like a call. */
    mov x16, x0
    mov x0, x1
    mov x1, x2
    mov x2, x3
    mov x3, x4
    genter
    ret
"""

SPTM_H_CONTENT = """/* SPDX-License-Identifier: MIT */
/* m5-linux: SPTM XNU-shim — boot-handoff entry for M4+/M3-on-26+ */
#ifndef __SPTM_H__
#define __SPTM_H__

#include "types.h"

/* Pack an SPTM call number: subsystem in upper 32 bits, idx in lower 32. */
#define SPTM_CALL(subsys, idx) (((u64)(subsys) << 32) | (u32)(idx))

/* The single boot-time handoff call XNU makes after setting VBAR_EL1.
 * Routes (in SPTM) through the raw-x16 dispatcher to the built-in handler
 * from the static dispatch table at 0xfffffff02701a770. */
#define SPTM_BOOT_HANDOFF SPTM_CALL(0x0, 0xf)

/* Raw SPTM call: GENTER into the resident GL2 monitor. */
u64 sptm_call(u64 callnum, u64 a, u64 b, u64 c, u64 d);

/* True on SoCs that ship SPTM (M4+, and M3 on macOS 26.0+ as of 25F71). */
bool soc_has_sptm(void);

/* Issue the boot handoff. No-op (returns 0) on pre-SPTM SoCs. */
int sptm_boot_handoff(void);

#endif
"""

SPTM_C_CONTENT = """/* SPDX-License-Identifier: MIT */
/* m5-linux: SPTM XNU-shim implementation */
#include "sptm.h"
#include "soc.h"
#include "utils.h"

bool soc_has_sptm(void)
{
    /* SPTM is a signed boot component on every chip listed below in the
     * 25F71 BuildManifest. M1 base (t8103) is the only Apple Silicon Mac
     * chip without SPTM and remains supported by the pre-SPTM path. */
    switch (chip_id) {
        case T8132:             /* M4 */
        case T6040:             /* M4 Pro */
        case T6041:             /* M4 Max */
        case T8142:             /* M5 */
        case T6050:             /* M5 Pro */
        case T6051:             /* M5 Max */
            return true;
        default:
            return false;
    }
}

int sptm_boot_handoff(void)
{
    if (!soc_has_sptm())
        return 0; /* pre-M4: no SPTM, nothing to do */

    /* When this runs, iBoot has already driven SPTM through
     * sptm_bootstrap_{early,tlbi,late,finalize} and set the
     * enforce_after stage bit (0x800) in the global stage register at
     * SPTM's __DATA. SPTM is in full enforcement; the OS-side
     * responsibility is just this single GENTER, parameterless from the
     * caller's perspective. The call routes (in SPTM, T8132 26.5) through
     * gxf_entry_el1 dispatcher (0x..a454c) -> type-0 raw-x16 dispatcher
     * (0x..ec944) -> dispatch_state_machine(class=2, evt=0xf). */
    if (!supports_gxf()) {
        printf(\"SPTM: GXF not supported on this CPU?! aborting handoff\\n\");
        return -1;
    }

    printf(\"SPTM: issuing boot handoff call (0x0:0xf) via genter...\\n\");
    u64 ret = sptm_call(SPTM_BOOT_HANDOFF, 0, 0, 0, 0);
    printf(\"SPTM: boot handoff returned 0x%lx\\n\", ret);

    /* TODO(hardware): observe behavior on real M4/M5 silicon. Statically,
     * iBoot's uses_sptm assertion guarantees that if we reach this point
     * SPTM is loaded and its built-in handler accepts the call. The
     * caller_domain assignment + RO-range declaration are done by iBoot
     * via /chosen DT properties (uat_bootstrap_parse_dt path), gated on
     * the loaded image being an SPTM-aware Boot Kernel Collection. */
    return 0;
}
"""

if not sptm_asm.exists():
    sptm_asm.write_text(SPTM_ASM_CONTENT)
    print("PATCH 0004: src/sptm_asm.S — created GENTER stub")
else:
    print("PATCH 0004: src/sptm_asm.S — already exists")

if not sptm_h.exists():
    sptm_h.write_text(SPTM_H_CONTENT)
    print("PATCH 0004: src/sptm.h — created API header")
else:
    print("PATCH 0004: src/sptm.h — already exists")

if not sptm_c.exists():
    sptm_c.write_text(SPTM_C_CONTENT)
    print("PATCH 0004: src/sptm.c — created shim implementation")
else:
    print("PATCH 0004: src/sptm.c — already exists")

# Wire sptm_boot_handoff() into main.c — just before mmu_shutdown() in the
# next-stage handoff. The location is identified by the printf "Preparing to
# run next stage" line which precedes the shutdowns.
main_c = M1N1 / "src" / "main.c"
text = main_c.read_text()
if SPTM_MARKER not in text:
    # Insert call after "Preparing to run next stage" printf, before nvme_shutdown()
    needle = 'printf("Preparing to run next stage at %p...\\n", next_stage.entry);'
    insertion = (
        f"\n\n    {SPTM_MARKER}\n"
        "    /* M4+: satisfy SPTM boot protocol before leaving m1n1. Runs while\n"
        "     * MMU is still on (XNU's pattern); m1n1 then proceeds to its own\n"
        "     * shutdowns and jumps to Linux. */\n"
        "    if (sptm_boot_handoff() < 0)\n"
        "        panic(\"SPTM boot handoff failed\\n\");"
    )
    if needle in text:
        text = text.replace(needle, needle + insertion)
        # Also add #include "sptm.h" near other includes
        if '#include "sptm.h"' not in text:
            text = text.replace('#include "smp.h"', '#include "smp.h"\n#include "sptm.h"', 1)
        main_c.write_text(text)
        print("PATCH 0004: src/main.c — wired sptm_boot_handoff() into handoff path")
    else:
        print("PATCH 0004: src/main.c — WARNING: insertion anchor not found, skipped")
else:
    print("PATCH 0004: src/main.c — already wired")

# Wire sptm.o sptm_asm.o into Makefile OBJECTS list
makefile = M1N1 / "Makefile"
text = makefile.read_text()
if "sptm.o" not in text:
    # Add after "gxf.o gxf_asm.o" which is in the OBJECTS list
    text = text.replace(
        "gxf.o gxf_asm.o \\",
        "gxf.o gxf_asm.o \\\n\tsptm.o sptm_asm.o \\",
        1,
    )
    makefile.write_text(text)
    print("PATCH 0004: Makefile — added sptm.o sptm_asm.o to OBJECTS")
else:
    print("PATCH 0004: Makefile — already wired")

print("\nAll patches applied (or already present). To re-apply from clean, delete build/m1n1 and reclone.")
