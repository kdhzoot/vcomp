#!/usr/bin/env python3
"""13(+1)설정 baseline vs F2Load: populated level별 총 SST 크기 편차."""
import subprocess, re, os, statistics, json
OLD='/work/vcomp/exp/eval_20260909_night1/'; NB='/work/vcomp/exp/nobitmap_remaining_20260914_run1/'
NB2='/work/vcomp/exp/nobitmap_20260914/'; GU='/work/vcomp/exp/global_unique_20260914/'; BM='/work/vcomp/exp/fix_20260913/'
PTS=[('P0 (1 KB, 1 TB)',OLD+'P0-r1_baseline',NB+'P0fix-r1_f2load',1048576000,24,1000),
     ('KV 100 B',OLD+'E1-100B_baseline',NB+'E1-100B_f2load',1048576000,24,76),
     ('KV 10 KB',OLD+'E1-10KB_baseline',NB+'E1-10KB_f2load',1048576000,24,10216),
     ('2 TB',OLD+'E2-2T_baseline',NB+'E2-2T_f2load',2097152000,24,1000),
     ('4 TB',OLD+'E2-4T_baseline',NB+'E2-4T_f2load',4194304000,24,1000),
     ('8 TB',OLD+'E2-8T_baseline',NB+'E2-8T_f2load',8388608000,24,1000),
     ('unique 25%',OLD+'E3-u25_baseline',NB2+'E3-u25_f2load',1048576000,24,1000),
     ('unique 50%',OLD+'E3-u50_baseline',NB2+'E3-u50_f2load',1048576000,24,1000),
     ('unique 100%',OLD+'E3-u100_baseline','/work/vcomp/exp/deepfirst_20260915/u100-deep_f2load',1048576000,24,1000),
     ('LZ4',OLD+'E7-lz4_baseline',NB+'E7-lz4_f2load',1048576000,24,1000),
     ('256MB x4',OLD+'E4-256m4_baseline',NB+'E4-256m4_f2load',1048576000,24,1000),
     ('1024MB x10',OLD+'E4-1024m10_baseline',NB+'E4-1024m10_f2load',1048576000,24,1000),
     ('SST 16 MB',BM+'E5-sst16_baseline',BM+'E5-sst16_f2load',1048576000,24,1000),
     ('SST 256 MB',BM+'E5-sst256_baseline',BM+'E5-sst256_f2load',1048576000,24,1000)]
def levels(db,num,key,value):
    out=subprocess.run(['./db_bench','--benchmarks=coverage',f'--db={db}','--use_existing_db=1','--readonly=true',
                        f'--num={num}',f'--key_size={key}',f'--value_size={value}','--compression_type=none',
                        '--format_version=7','--bloom_bits=10','--disable_auto_compactions=true'],
                       capture_output=True,text=True).stdout
    lv={}
    for line in out.splitlines():
        p=line.split()
        if len(p)>=3 and p[0].isdigit() and p[1].isdigit() and re.match(r'^[\d.]+$',p[2]):
            lv[int(p[0])]=(int(p[1]),float(p[2]))   # files, MiB
    return lv
allrel=[]; rows=[]
print(f"{'설정':15s} {'level':>5s} {'base MiB':>11s} {'F2 MiB':>11s} {'Δ':>9s}  {'GiB 비중':>8s}")
for n,b,f,num,k,v in PTS:
    if not (os.path.exists(b+'/CURRENT') and os.path.exists(f+'/CURRENT')): continue
    B,F=levels(b,num,k,v),levels(f,num,k,v)
    tot=sum(s for _,s in B.values())
    for l in sorted(set(B)|set(F)):
        bs=B.get(l,(0,0.0))[1]; fs=F.get(l,(0,0.0))[1]
        rel=(fs/bs-1) if bs>0 else float('inf')
        rows.append((n,l,bs,fs,rel,bs/tot))
        if bs>0: allrel.append((abs(rel),n,l,bs,fs,bs/tot))
        print(f"{n:15s} {'L'+str(l):>5s} {bs:11.1f} {fs:11.1f} "
              f"{(f'{100*rel:+8.2f}%' if bs>0 else '   신규   ')}  {100*bs/tot:7.1f}%")
print()
a=[x[0] for x in allrel]
print(f"  populated level 전체 ({len(a)}개): 최대 |Δ| {100*max(a):.2f}%, 중앙 {100*statistics.median(a):.2f}%, 평균 {100*statistics.mean(a):.2f}%")
big=[x for x in allrel if x[5]>=0.01]
ab=[x[0] for x in big]
print(f"  DB의 1% 이상인 level만 ({len(ab)}개): 최대 |Δ| {100*max(ab):.2f}%, 중앙 {100*statistics.median(ab):.2f}%")
print("\n  상위 편차:")
for d,n,l,bs,fs,share in sorted(allrel,reverse=True)[:6]:
    print(f"    {n:15s} L{l}  {bs:9.1f} -> {fs:9.1f} MiB  ({100*(fs/bs-1):+6.2f}%)  DB의 {100*share:.1f}%")
json.dump([dict(config=n,level=l,base_mib=bs,f2_mib=fs,rel=rel,share=sh) for n,l,bs,fs,rel,sh in rows],
          open('experiments/results/per_level_stats_260915.json','w'),indent=1)
print('\n  saved experiments/results/per_level_stats_260915.json')
