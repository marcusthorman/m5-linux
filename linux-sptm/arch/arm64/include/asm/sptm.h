/* SPDX-License-Identifier: GPL-2.0 */
/*
 * arch/arm64/include/asm/sptm.h
 *
 * Linux interface to the Apple Secure Page Table Monitor (SPTM).
 *
 * SPTM is a GL2 (above EL2) monitor introduced with macOS 14.x/26.x on Apple
 * Silicon Macs (signed boot component on M2/M3/M4/M5; M1 base t8103 is the
 * only chip without it). When SPTM is active, every page-table mutation by
 * the running OS must go through `sptm_uat_*` calls — direct PT writes are
 * blocked. This header defines the wrapper API; arch/arm64/mm/sptm.c contains
 * the implementation.
 *
 * Linux runs as caller_domain = XNU (the only "OS-kernel" supervisor mode
 * SPTM exposes). Pages are classified using the XNU_* frame types below.
 *
 * Build status: scaffold only. Most wrappers are stubs marked TODO.
 * See docs/linux-mmu-port-scope.md in the m5-linux repo for the work plan.
 */
#ifndef __ASM_SPTM_H
#define __ASM_SPTM_H

#include <linux/types.h>

#ifdef CONFIG_ARM64_APPLE_SPTM

/*
 * Pack an SPTM call number: subsys in upper 32 bits, idx in lower 32.
 * This is the value SPTM reads from x16 at the GENTER entry.
 */
#define SPTM_PACK(subsys, idx) (((u64)(subsys) << 32) | (u32)(idx))

/* Boot-time handoff event (parameterless from caller side). */
#define SPTM_BOOT_HANDOFF SPTM_PACK(0x0, 0xf)

/*
 * Subsys-0 calls — labels recovered from XNU caller-function names.
 *
 * Confidence legend:
 *   CONFIRMED — externally-named XNU stub or earlier-RE'd
 *   HIGH      — caller function unambiguously identifies the op
 *   MED       — caller hints; verify against XNU caller-side context
 *   LOW       — generic caller; needs hardware confirmation
 *   TODO      — XNU caller unknown / verify on hardware
 *
 * See analysis/sptm/sptm-subsys0-labels.csv in the m5-linux repo.
 */
#define SPTM_CALL_RETYPE                 SPTM_PACK(0x0, 0x01)  /* CONFIRMED: sptm_retype */
#define SPTM_CALL_MAP_OR_UAT_MAP_TABLE   SPTM_PACK(0x0, 0x02)  /* MED: sptm_map_page or sptm_uat_map_table */
#define SPTM_CALL_UAT_GET_INFO           SPTM_PACK(0x0, 0x03)  /* MED: sptm_uat_get_info */
#define SPTM_CALL_ROOT_TABLE_PADDR       SPTM_PACK(0x0, 0x04)  /* MED+: uat_state_get_root_table_paddr */
#define SPTM_CALL_UPDATE_REGION          SPTM_PACK(0x0, 0x05)  /* MED+: sptm_update_region */
#define SPTM_CALL_DROP_TABLE_REFCNTS     SPTM_PACK(0x0, 0x06)  /* LOW: sptm_drop_table_refcnts / get_info */
#define SPTM_CALL_DISJOINT_OP            SPTM_PACK(0x0, 0x07)  /* LOW: sptm_disjoint_op */
#define SPTM_CALL_UPDATE_DISJOINT        SPTM_PACK(0x0, 0x08)  /* LOW: sptm_update_disjoint */
#define SPTM_CALL_CONFIGURE_SHARED_REGION SPTM_PACK(0x0, 0x09) /* HIGH: sptm_configure_shared_region */
#define SPTM_CALL_UNNEST_REGION          SPTM_PACK(0x0, 0x0a)  /* HIGH: sptm_unnest_region */
#define SPTM_CALL_NEST_OR_UNNEST         SPTM_PACK(0x0, 0x0b)  /* MED */
#define SPTM_CALL_SET_SHARED_REGION      SPTM_PACK(0x0, 0x0c)  /* MED */
#define SPTM_CALL_SWITCH_ROOT            SPTM_PACK(0x0, 0x0d)  /* HIGH: sptm_switch_root */
#define SPTM_CALL_CPU_TOPOLOGY_QUERY     SPTM_PACK(0x0, 0x0e)  /* CONFIRMED (research doc) */
#define SPTM_CALL_BOOT_HANDOFF_ALT       SPTM_PACK(0x0, 0x0f)  /* CONFIRMED: boot handoff */
#define SPTM_CALL_SLIDE_REGION           SPTM_PACK(0x0, 0x10)  /* MED */
#define SPTM_CALL_SET_SHARED_REGION_ALT  SPTM_PACK(0x0, 0x11)  /* MED */
#define SPTM_CALL_REGISTER_CPU_OR_INIT   SPTM_PACK(0x0, 0x12)  /* HIGH (boot): sptm_register_cpu */
#define SPTM_CALL_CPU_INIT_AUX           SPTM_PACK(0x0, 0x13)  /* HIGH (boot): sptm_n_cpus / cpu_init aux */
/*                                       SPTM_PACK(0x0, 0x14)   LOW: debug/IRQ helper */
#define SPTM_CALL_MAP_PAGE               SPTM_PACK(0x0, 0x15)  /* HIGH: sptm_map_page (leaf PTE install) */
/*           0x16..0x1d                  no XNU callers — not in Linux's API surface */
#define SPTM_CALL_UAT_GET_ROOT_TABLE_PADDR SPTM_PACK(0x0, 0x1e) /* HIGH: uat_state_get_root_table_paddr */
#define SPTM_CALL_REGISTER_IO_FRAME      SPTM_PACK(0x0, 0x1f)  /* MED+: sptm_register_io_frame */
#define SPTM_CALL_REGISTER_IO_FRAME_FREE SPTM_PACK(0x0, 0x20)  /* MED+: io frame release */
#define SPTM_CALL_REGISTER_XNU_EXC_RETURN SPTM_PACK(0x0, 0x21) /* MED: sptm_register_xnu_exc_return */
/*           0x22..0x26                  no XNU callers */
#define SPTM_CALL_NEST_REGION            SPTM_PACK(0x0, 0x27)  /* MED: sptm_nest_region */
#define SPTM_CALL_NEST_REGION_ALT        SPTM_PACK(0x0, 0x28)  /* MED: paired with 0x27 */
/*           0x29..0x2a                  no XNU callers */
#define SPTM_CALL_UAT_UNMAP_TABLE        SPTM_PACK(0x0, 0x2b)  /* HIGH: sptm_uat_unmap_table */
#define SPTM_CALL_UNMAP_TABLE            SPTM_PACK(0x0, 0x2c)  /* HIGH: sptm_unmap_table / unmap_continue */
/*                                       SPTM_PACK(0x0, 0x2d)   LOW: debug/IRQ helper */
#define SPTM_CALL_REGISTER_XNU_EXC_RETURN_ALT SPTM_PACK(0x0, 0x2e) /* MED+ */
/*           0x2f..0x31                  no XNU callers */

/* Subsys 0xb (gen3dart/IOMMU) — fully named, used by Apple-DART driver. */
#define SPTM_CALL_GEN3DART_MAP_TABLE     SPTM_PACK(0xb, 0x00)
#define SPTM_CALL_GEN3DART_UNMAP_TABLE   SPTM_PACK(0xb, 0x01)
#define SPTM_CALL_GEN3DART_MAP           SPTM_PACK(0xb, 0x02)
#define SPTM_CALL_GEN3DART_UNMAP         SPTM_PACK(0xb, 0x03)
#define SPTM_CALL_GEN3DART_POWERDOWN     SPTM_PACK(0xb, 0x04)
#define SPTM_CALL_GEN3DART_POWERUP       SPTM_PACK(0xb, 0x05)
#define SPTM_CALL_GEN3DART_INIT          SPTM_PACK(0xb, 0x06)
#define SPTM_CALL_GEN3DART_DISABLE_TRANSLATION SPTM_PACK(0xb, 0x07)
#define SPTM_CALL_GEN3DART_ENABLE_TRANSLATION  SPTM_PACK(0xb, 0x08)
/* Remaining gen3dart calls 0x09..0x12 omitted here; full list in sptm-call-numbers.csv. */

/*
 * SPTM frame-type taxonomy. Every physical page in the running OS's view
 * carries one of these types; SPTM enforces what can be written to which
 * type, and only certain transitions are allowed (via sptm_retype).
 *
 * Linux uses the XNU_* set since it runs as caller_domain = XNU.
 */
enum sptm_frame_type {
	SPTM_FRAME_XNU_DEFAULT          = 0,  /* generic RW page */
	SPTM_FRAME_XNU_RO               = 1,  /* read-only data */
	SPTM_FRAME_XNU_ROZONE           = 2,  /* read-only zone */
	SPTM_FRAME_XNU_RO_DBG_RW        = 3,  /* RO normally, RW in debug */
	SPTM_FRAME_XNU_KERNEL_RESTRICTED = 4,
	SPTM_FRAME_XNU_PAGE_TABLE       = 5,  /* general PT page */
	SPTM_FRAME_XNU_PAGE_TABLE_SHARED = 6,
	SPTM_FRAME_XNU_PAGE_TABLE_COMMPAGE = 7,
	SPTM_FRAME_XNU_PAGE_TABLE_ROZONE = 8,
	SPTM_FRAME_XNU_STAGE2_PAGE_TABLE = 9,  /* KVM stage-2 PT */
	SPTM_FRAME_XNU_STAGE2_ROOT_TABLE = 10,
	SPTM_FRAME_XNU_SHARED_ROOT_TABLE = 11,
	SPTM_FRAME_XNU_USER_ROOT_TABLE  = 12,
	SPTM_FRAME_XNU_SUBPAGE_USER_ROOT_TABLES = 13,
	SPTM_FRAME_XNU_USER_EXEC        = 14,
	SPTM_FRAME_XNU_USER_JIT         = 15,
	SPTM_FRAME_XNU_USER_DEBUG       = 16,
	SPTM_FRAME_XNU_USER_TPRO        = 17,
	SPTM_FRAME_XNU_IO               = 18,
	SPTM_FRAME_XNU_IOMMU            = 19,
	SPTM_FRAME_XNU_PROTECTED_IO     = 20,
	SPTM_FRAME_XNU_RESTRICTED_IO    = 21,
	SPTM_FRAME_XNU_COMMPAGE_RO      = 22,
	SPTM_FRAME_XNU_COMMPAGE_RW      = 23,
	SPTM_FRAME_XNU_COMMPAGE_RX      = 24,
	SPTM_FRAME_XNU_TAG_STORAGE      = 25,
	SPTM_FRAME_XNU_COPROCESSOR_RO_IO = 26,
	/* The exact numeric values here are TODO from hardware-side observation;
	 * SPTM's binary uses these names but the enum values aren't directly
	 * exposed. Implementer must extract them from XNU's pmap_arm.c. */
};

/*
 * SPTM exec modes (the "caller_domain" enumeration). Linux uses
 * SPTM_EXEC_XNU_DEFAULT for kernel-mode execution.
 */
enum sptm_exec_mode {
	SPTM_EXEC_SPTM_DEFAULT = 0,
	SPTM_EXEC_TXM_DEFAULT  = 1,
	SPTM_EXEC_XNU_DEFAULT  = 2,
	SPTM_EXEC_XNU_ROZONE_RW = 3,
	SPTM_EXEC_XNU_USER_DEFAULT = 4,
	SPTM_EXEC_XNU_USER_JIT_RW = 5,
	SPTM_EXEC_XNU_USER_TPRO_RW = 6,
	/* TODO(hardware): exact numeric values; SPTM's binary names them
	 * but the enum values must be observed from XNU side. */
};

/*
 * Raw SPTM call. Issues GENTER with the packed call number in x16 and up
 * to four argument registers. Returns SPTM's reply in x0.
 *
 * Defined in arch/arm64/mm/sptm.c (inline asm for the GENTER).
 */
u64 sptm_call(u64 callnum, u64 a, u64 b, u64 c, u64 d);

/* True iff this CPU's SoC has SPTM in its boot chain. */
bool sptm_present(void);

/* Boot-time entry: issue the (0x0:0xf) handoff. Called once per BSP. */
int sptm_boot_handoff(void);

/* --- High-level wrappers (declarations; implementations TBD per call) --- */

/* Retype a physical page from one frame type to another. SPTM validates the
 * transition and updates ownership. paddr must be 16 KB aligned (SPTM's
 * page granule on Apple Silicon). */
int sptm_retype(phys_addr_t paddr, enum sptm_frame_type from,
		enum sptm_frame_type to);

/* Install a leaf PTE. SPTM-side validates that the target frame's type is
 * compatible with the requested mapping permissions. */
int sptm_set_pte(phys_addr_t ptep, u64 pte_val);

/* Install / remove a non-leaf table at a given level. */
int sptm_uat_map_table(phys_addr_t parent_pte, phys_addr_t child_table,
		       unsigned int level);
int sptm_uat_unmap_table(phys_addr_t parent_pte);

/*
 * Opaque per-mm SPTM state handle. Allocated by sptm_uat_init_state()
 * during init_new_context(); passed as the leading arg to nearly every
 * UAT call (the SPTM-side handler reads x0 as a sanitized state pointer
 * via the validator at 0xfffffff0270c9604).
 *
 * TODO(impl): wire into struct mm_struct (probably arch-specific field
 * arch_struct_mm or a side-table) and initialize during context creation.
 */
struct sptm_uat_state;

/* Get a state object's root-table paddr for a given ASID. */
phys_addr_t sptm_uat_get_root_table_paddr(struct sptm_uat_state *state, u16 asid);

/* TLB invalidation — replaces direct TLBI from EL1. */
void sptm_broadcast_tlbi_all(void);
void sptm_broadcast_tlbi_asid(u16 asid);
void sptm_broadcast_tlbi_va(unsigned long va, u16 asid);

/* Switch the active page-table root (TTBR1/TTBR0). Replaces direct msr ttbr*. */
int sptm_switch_root(phys_addr_t new_root, u16 asid);

/* Per-CPU registration during secondary bring-up. */
int sptm_register_cpu(unsigned int cpu);

#else  /* !CONFIG_ARM64_APPLE_SPTM */

/* Stubs for builds without SPTM support — preserve pre-SPTM hardware
 * (M1 base, plus M2/M3 on older macOS) behavior unchanged. */
static inline bool sptm_present(void) { return false; }
static inline int sptm_boot_handoff(void) { return 0; }
static inline int sptm_register_cpu(unsigned int cpu) { return 0; }

#endif /* CONFIG_ARM64_APPLE_SPTM */

#endif /* __ASM_SPTM_H */
