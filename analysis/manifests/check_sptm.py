import plistlib,sys
pl=plistlib.load(open(sys.argv[1],'rb'))
want={'0x8122':'M3','0x6030':'M3Pro','0x6031':'M3Max','0x8132':'M4','0x8142':'M5','0x8112':'M2','0x8103':'M1'}
seen={}
for ident in pl.get('BuildIdentities',[]):
    chip=str(ident.get('ApChipID','?'))
    if chip not in want: continue
    mani=ident.get('Manifest',{})
    has=any('SecurePageTableMonitor' in k for k in mani)
    # only record the OS restore variant (not Recovery/Restore-only) preferring presence
    seen[chip]=seen.get(chip,False) or has
ver=pl.get('ProductVersion','?'); bld=pl.get('ProductBuildVersion','?')
print(f"{ver} ({bld}):", ", ".join(f"{want[c]}={'SPTM' if seen.get(c) else 'none'}" for c in want if c in seen))
