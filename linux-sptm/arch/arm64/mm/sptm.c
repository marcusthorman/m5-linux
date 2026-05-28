// SPDX-License-Identifier: GPL-2.0
/*
 * arch/arm64/mm/sptm.c
 *
 * Apple Secure Page Table Monitor (SPTM) wrapper layer.
 *
 * SPTM is a GL2 (above EL2) monitor that owns the page tables on Apple
 * Silicon Macs running macOS 14.x and newer (signed boot component on
 * M2/M3/M4/M5 — only M1 base t8103 lacks it). Once iBoot has driven SPTM
 * past the `enforce_after` bootstrap stage, every PT mutation in the
 * running OS must go through this wrapper layer.
 *
 * Architecture: Linux runs as caller_domain = XNU (the only OS-kernel
 * supervisor mode SPTM exposes). The wrapper translates Linux's pgtable
 * operations into the SPTM call ABI: `genter` with the packed call number
 * in x16 and args in x0..x3.
 *
 * Status: scaffold. Most wrappers are stubs that build but don't yet
 * implement the real call semantics — see the per-function TODOs and
 * docs/linux-mmu-port-scope.md.
 */

#include <linux/init.h>
#include <linux/types.h>
#include <linux/printk.h>
#include <linux/cpu.h>
#include <asm/sptm.h>

/*
 * The raw GENTER stub. Apple's `genter` instruction is encoded as
 * 0x00201420; disassemblers that don't know Apple's ISA render it as
 * `udf #0`. SPTM has installed its GL2 dispatcher at GXF_ENTER_EL1 during
 * iBoot's sptm_bootstrap_*; a bare GENTER from EL1 lands there.
 *
 * Calling convention (matches XNU's _sptm_* stubs):
 *   x16 = packed call number (subsys << 32 | idx)
 *   x0..x3 = arguments
 *   x0 = return value
 */
noinline u64 sptm_call(u64 callnum, u64 a, u64 b, u64 c, u64 d)
{
	register u64 x16 asm("x16") = callnum;
	register u64 x0  asm("x0")  = a;
	register u64 x1  asm("x1")  = b;
	register u64 x2  asm("x2")  = c;
	register u64 x3  asm("x3")  = d;

	asm volatile(".long 0x00201420\n"   /* GENTER */
		     : "+r"(x0), "+r"(x1), "+r"(x2), "+r"(x3)
		     : "r"(x16)
		     : "memory", "cc");
	return x0;
}

/* True iff this SoC has SPTM in its boot chain.
 *
 * Decision basis: presence of `Ap,SecurePageTableMonitor` in the
 * BuildManifest of the running macOS for this chip. We can't read the
 * manifest from Linux; instead we rely on the device-tree compatible at
 * the root, which Asahi already parses. For the initial scaffold we
 * compile-gate on CONFIG_ARM64_APPLE_SPTM and assume present; future
 * work: a runtime DT check (e.g. /chosen/sptm-* properties iBoot writes).
 *
 * TODO(implementation): read /chosen for SPTM-* properties to confirm.
 */
bool sptm_present(void)
{
	return IS_ENABLED(CONFIG_ARM64_APPLE_SPTM);
}

/*
 * Issue the boot-time (0x0:0xf) handoff. Called once per BSP early in
 * secondary_start_kernel-like boot path.
 *
 * Per static RE: by the time the OS runs, iBoot has already set the
 * `enforce_after` bit in SPTM's stage register (T8132 26.5: writer at
 * sptm_bootstrap_late, atomic-or-set at file offset 0x..e4f68). The OS-
 * side handoff is parameterless and acts as a state-machine event
 * acknowledging "OS is ready".
 *
 * TODO(hardware): observe return value on real silicon. A successful
 * static dispatch is presumed; if SPTM panics, the cause is most likely
 * the loaded image's kc_layout — m1n1 must be packaged as an SPTM-aware
 * Boot Kernel Collection for iBoot to register it with SPTM in the
 * first place (see docs/sptm-research.md).
 */
int __init sptm_boot_handoff(void)
{
	u64 ret;

	if (!sptm_present())
		return 0;

	pr_info("sptm: issuing (0x0:0xf) boot handoff via GENTER\n");
	ret = sptm_call(SPTM_BOOT_HANDOFF, 0, 0, 0, 0);
	pr_info("sptm: handoff returned 0x%llx\n", ret);

	return 0;
}

/* ============================================================
 *   High-level wrappers — Linux MMU primitives → SPTM calls
 *
 *   All bodies below are scaffold. They invoke the call but make no
 *   claims about the argument layout matching SPTM's expectations —
 *   that needs per-call verification against XNU's caller-side context
 *   in pmap_arm.c (see analysis/sptm/sptm-subsys0-labels.csv).
 * ============================================================ */

int sptm_retype(phys_addr_t paddr, enum sptm_frame_type from,
		enum sptm_frame_type to)
{
	/* CONFIRMED: (0x0, 0x01).
	 * TODO(verify): argument layout. XNU's pmap_arm.c calls _sptm_retype
	 * with paddr in x0 and a packed (from << N | to) descriptor; exact
	 * encoding TBD from XNU caller-side. */
	u64 desc = ((u64)from << 32) | (u64)to;
	return (int)sptm_call(SPTM_CALL_RETYPE, (u64)paddr, desc, 0, 0);
}

int sptm_set_pte(phys_addr_t ptep, u64 pte_val)
{
	/* HIGH: (0x0, 0x15) — sptm_map_page, leaf PTE install. Heavy use in
	 * XNU's pa_get_ptd (7+ call sites). */
	return (int)sptm_call(SPTM_CALL_MAP_PAGE, (u64)ptep, pte_val, 0, 0);
}

int sptm_uat_map_table(phys_addr_t parent_pte, phys_addr_t child_table,
		       unsigned int level)
{
	/* MED: (0x0, 0x02) — sptm_map_page / sptm_uat_map_table.
	 * TODO(verify): is this really map_table or map_page? Two distinct
	 * SPTM ops; caller context in XNU disambiguates. */
	return (int)sptm_call(SPTM_CALL_MAP_OR_UAT_MAP_TABLE,
			      (u64)parent_pte, (u64)child_table, level, 0);
}

int sptm_uat_unmap_table(phys_addr_t parent_pte)
{
	/* HIGH: (0x0, 0x2b) — sptm_uat_unmap_table.
	 * Sole XNU caller: pmap_remove_options_internal. */
	return (int)sptm_call(SPTM_CALL_UAT_UNMAP_TABLE, (u64)parent_pte, 0, 0, 0);
}

void sptm_broadcast_tlbi_all(void)
{
	/* TODO(label): the precise (subsys, idx) for sptm_broadcast_tlbi is
	 * not yet pinned. The most likely candidates are in subsys 0 with
	 * idxs around 0x14 or 0x2d (the `ml_set_interrupts_enabled_with_debug`
	 * cluster — TLBI is often paired with IRQ-disable). Verify by reading
	 * XNU's pmap_arm tlb_flush_*. */
	pr_warn_once("sptm: broadcast_tlbi_all stub — not yet implemented\n");
}

void sptm_broadcast_tlbi_asid(u16 asid)
{
	/* TODO(label): same as above; ASID-scoped TLBI. */
	pr_warn_once("sptm: broadcast_tlbi_asid stub — not yet implemented\n");
	(void)asid;
}

void sptm_broadcast_tlbi_va(unsigned long va, u16 asid)
{
	/* TODO(label): VA+ASID TLBI. */
	pr_warn_once("sptm: broadcast_tlbi_va stub — not yet implemented\n");
	(void)va; (void)asid;
}

int sptm_switch_root(phys_addr_t new_root, u16 asid)
{
	/* HIGH: (0x0, 0x0d) — sptm_switch_root.
	 * Sole XNU caller: pmap_switch_internal. */
	return (int)sptm_call(SPTM_CALL_SWITCH_ROOT, (u64)new_root, asid, 0, 0);
}

int sptm_register_cpu(unsigned int cpu)
{
	/* HIGH (boot): (0x0, 0x12) — sptm_register_cpu / cpu_init.
	 * Sole XNU caller: arm_init. Must be called from each secondary CPU
	 * early in its bring-up path. */
	if (!sptm_present())
		return 0;
	return (int)sptm_call(SPTM_CALL_REGISTER_CPU_OR_INIT, cpu, 0, 0, 0);
}
