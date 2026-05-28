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
 *   CRITICAL ARGUMENT-LAYOUT PATTERN (from SPTM-side handler RE,
 *   T8132 26.5): every handler's x0 is a "state object pointer"
 *   that gets sanitized by a validator at 0xfffffff0270c9604 before
 *   the real work happens. The remaining x1..xN are the operation-
 *   specific args. This means every Linux wrapper needs a per-mm
 *   "SPTM state handle" stored somewhere (struct mm_struct extension)
 *   that we pass as the first arg.
 *
 *   TODO(impl): add `struct sptm_uat_state *` field to arm64's
 *   pgd_t-backing or to a side-table keyed by mm, initialized in
 *   init_new_context() (which calls sptm_uat_init_state).
 *
 *   Confirmed handler prologues:
 *     sptm_retype:                    x0=paddr (16K-aligned),
 *                                     x1=new_type, x2=old_type
 *     sptm_uat_map_table:             x0=state, x1=parent_pte,
 *                                     x2=child_table, x3=level
 *     sptm_uat_unmap_table:           x0=state, x1, x2
 *     uat_state_get_root_table_paddr: x0=state, x1=asid (bound 0x40)
 *     sptm_map_page:                  x0=state, x1=paddr_or_pte,
 *                                     x2=addr, x3=perm (bound 0x2)
 *     sptm_switch_root:               x0=state, x1=flags, x2=arg
 *                                     (must be in GL2; reads TTBR0_EL1
 *                                     for save/restore)
 *     sptm_register_cpu:              x0=cpu_descriptor (boot context)
 *
 *   Other wrappers below still TODO — see per-function notes.
 * ============================================================ */

/*
 * sptm_uat_state — opaque handle to SPTM's per-address-space state.
 *
 * Allocated by sptm_uat_init_state at mm creation; passed as the first
 * arg to nearly every UAT call. Embedded in mm_struct via an arch-specific
 * field, or kept in a side-table keyed by mm pointer.
 *
 * The state object's layout is opaque to Linux (SPTM owns it). We only
 * hold a paddr-equivalent handle. Field offsets observed in SPTM-side
 * (from `uat_instance->...` strings): handoff_region.micro_magic,
 * handoff_region.powered_off, ttbat_region.paddr, state_object_size,
 * mode (uat_instance->mode), context_id (state_object->context_id).
 */
struct sptm_uat_state;

int sptm_retype(phys_addr_t paddr, enum sptm_frame_type from,
		enum sptm_frame_type to)
{
	/* CONFIRMED: (0x0, 0x01). Verified signature from SPTM-side handler
	 * at 0xfffffff0270f2b10 (T8132 26.5):
	 *   x0 = paddr (validated as 16 KB-aligned, range-checked vs
	 *               two globals at __DATA+0xd00 and +0xd08 — likely
	 *               sptm_first_phys / sptm_last_phys)
	 *   x1 = new_type (low 8 bits; bound: < 0x42)
	 *   x2 = old_type (low 8 bits; bound: < 0x42)
	 *
	 * Note: paddr is the *physical address of the frame being retyped*,
	 * not a pointer to a parameter struct. The SPTM strings reference
	 * `retype_params_U.raw_U` which is a verified-pointer wrapper used
	 * elsewhere; the actual handler reads paddr/new/old as separate args.
	 *
	 * Note: SPTM_FRAME_* enum values are NOT directly the values SPTM
	 * uses. The 0x42 bound suggests SPTM has ~66 distinct frame-type
	 * values. The mapping between our enum order and SPTM's numeric
	 * values is TODO from hardware/caller-side observation.
	 */
	return (int)sptm_call(SPTM_CALL_RETYPE, (u64)paddr, (u64)to, (u64)from, 0);
}

int sptm_set_pte(phys_addr_t ptep, u64 pte_val)
{
	/* (0x0, 0x15). Verified handler at 0xfffffff0270f350c — but
	 * signature shows 4 args, NOT 2. SPTM-side handler reads:
	 *   x0 = state (sanitized by 0xfffffff0270f4100)
	 *   x1, x2, x3 = (saved as x21, x23, x20)
	 *   x3 low byte: bound < 0x2 (likely a perm/mode flag)
	 *
	 * So sptm_set_pte() needs at least:
	 *   state, paddr_or_pte, addr, perm_flag
	 *
	 * The current 2-arg wrapper is WRONG and will fail validation.
	 * TODO(impl): redesign signature once we know how Linux's
	 * __set_pte calls map onto this. The 4 args strongly suggest
	 * SPTM expects (state, paddr_to_install, vaddr_target, perms).
	 */
	pr_warn_once("sptm: sptm_set_pte() wrapper signature wrong; needs state+vaddr+perm rework\n");
	return (int)sptm_call(SPTM_CALL_MAP_PAGE, 0 /* state TODO */,
			      (u64)ptep, pte_val, 0);
}

int sptm_uat_map_table(phys_addr_t parent_pte, phys_addr_t child_table,
		       unsigned int level)
{
	/* (0x0, 0x02). Verified handler at 0xfffffff0270b993c:
	 *   x0 = state (sanitized by 0xfffffff0270c9604, w1=2 w2=0xf)
	 *   x1 = arg1 (saved as x22, later used as memory descriptor base
	 *              with size 0x4000 = 16 KB page)
	 *   x2 = arg2 (saved as x21)
	 *   x3 = arg3 (saved as x20)
	 *
	 * Confirmed it's a 4-arg call. Our (parent_pte, child_table, level)
	 * + the leading state object = 4 args total. Likely:
	 *   sptm_uat_map_table(state, parent_pte, child_table, level)
	 */
	pr_warn_once("sptm: sptm_uat_map_table() wrapper needs state arg added\n");
	return (int)sptm_call(SPTM_CALL_MAP_OR_UAT_MAP_TABLE,
			      0 /* state TODO */, (u64)parent_pte,
			      (u64)child_table, level);
}

int sptm_uat_unmap_table(phys_addr_t parent_pte)
{
	/* HIGH: (0x0, 0x2b). Verified handler at 0xfffffff0270b9640:
	 *   x0 = state (sanitized by 0xfffffff0270c9604, w1=2 w2=0xf)
	 *   x1 = arg1 (saved as x21)
	 *   x2 = arg2 (saved as x20)
	 *
	 * 3-arg call. Caller-side: pmap_remove_options_internal. Our
	 * 1-arg wrapper is missing the state + a second value.
	 * TODO(impl): redesign.
	 */
	pr_warn_once("sptm: sptm_uat_unmap_table() wrapper needs state + 2nd arg\n");
	return (int)sptm_call(SPTM_CALL_UAT_UNMAP_TABLE,
			      0 /* state TODO */, (u64)parent_pte, 0, 0);
}

phys_addr_t sptm_uat_get_root_table_paddr(struct sptm_uat_state *state, u16 asid)
{
	/* HIGH: (0x0, 0x1e). Verified handler `uat_state_get_root_table_paddr`
	 * at 0xfffffff0270b6f68:
	 *   x0 = state (sanitized by 0xfffffff0270c9604, w1=2 w2=5)
	 *   x1 = asid (16-bit; bound: < 0x40, suggesting per-state ASID
	 *              index space; the 0x10000 ASID limit is per-system)
	 * Returns: u16 read from [x0+0x18] — the root table paddr or an
	 * index into the TTBAT region. (TODO: confirm exact return shape.)
	 */
	return (phys_addr_t)sptm_call(SPTM_CALL_UAT_GET_ROOT_TABLE_PADDR,
				      (u64)state, asid, 0, 0);
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
	/* HIGH: (0x0, 0x0d). Verified handler at 0xfffffff0270fa6b8:
	 *   x0 = (used implicitly — possibly a CPU-local or state pointer)
	 *   x1 = flags/value (saved as x21, then `tst w21, #0xfea4` for
	 *        a mask check — packed flags + value)
	 *   x2 = arg (saved as x20)
	 *   Must be in GL2 (currentg != 0; cbz branches to error path).
	 *   Reads TTBR0_EL1 (saves x22) — operates on user-space root.
	 *
	 * This is operating on the *active* page-table root for the current
	 * CPU, not a generic switch operation. Our 2-arg wrapper is missing
	 * the leading state/CPU arg.
	 * TODO(impl): match against pmap_switch_internal caller-side context.
	 */
	pr_warn_once("sptm: sptm_switch_root() wrapper signature unverified\n");
	return (int)sptm_call(SPTM_CALL_SWITCH_ROOT,
			      0 /* state/cpu TODO */, (u64)new_root, asid, 0);
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
