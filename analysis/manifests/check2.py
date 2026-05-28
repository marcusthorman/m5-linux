import plistlib,sys
label={'0x8103':'M1','0x8112':'M2','0x8122':'M3','0x6000':'M1Pro','0x6020':'M2Pro','0x6030':'M3Pro','0x6031':'M3Max','0x8132':'M4','0x8142':'M5','0x6050':'M5Pro','0x8140':'A18Pro'}
pl=plistlib.load(open(sys.argv[1],'rb'))
agg={}
for ident in pl.get('BuildIdentities',[]):
    chip=str(ident.get('ApChipID','?'))
    mani=ident.get('Manifest',{})
    norm=any(k=='Ap,SecurePageTableMonitor' for k in mani)
    rest=any(k=='Ap,RestoreSecurePageTableMonitor' for k in mani)
    a=agg.setdefault(chip,[False,False]); a[0]|=norm; a[1]|=rest
print(f"{pl.get('ProductVersion','?')} ({pl.get('ProductBuildVersion','?')}):")
for c in sorted(agg):
    if c in label:
        n,r=agg[c]
        print(f"   {label[c]:7s} {c:8s} boot-SPTM={'Y' if n else '.'}  restore-SPTM={'Y' if r else '.'}")
