#!/usr/bin/env python3
"""13설정 baseline vs F2Load: DB size / SST count / avg SST size 편차."""
import subprocess, re, os, sys, statistics, json

OLD = '/work/vcomp/exp/eval_20260909_night1/'
NB  = '/work/vcomp/exp/nobitmap_remaining_20260914_run1/'
NB2 = '/work/vcomp/exp/nobitmap_20260914/'
GU  = '/work/vcomp/exp/global_unique_20260914/'
BM  = '/work/vcomp/exp/fix_20260913/'
# (라벨, baseline, F2Load, num, key, value)
PTS = [('P0 (1 KB, 1 TB)', OLD+'P0-r1_baseline', NB+'P0fix-r1_f2load', 1048576000, 24, 1000),
       ('KV 100 B',   OLD+'E1-100B_baseline',   NB+'E1-100B_f2load',   1048576000, 24, 76),
       ('KV 10 KB',   OLD+'E1-10KB_baseline',   NB+'E1-10KB_f2load',   1048576000, 24, 10216),
       ('2 TB',       OLD+'E2-2T_baseline',     NB+'E2-2T_f2load',     2097152000, 24, 1000),
       ('4 TB',       OLD+'E2-4T_baseline',     NB+'E2-4T_f2load',     4194304000, 24, 1000),
       ('8 TB',       OLD+'E2-8T_baseline',     NB+'E2-8T_f2load',     8388608000, 24, 1000),
       ('unique 25%', OLD+'E3-u25_baseline',    NB2+'E3-u25_f2load',   1048576000, 24, 1000),
       ('unique 50%', OLD+'E3-u50_baseline',    NB2+'E3-u50_f2load',   1048576000, 24, 1000),
       ('unique 100%',OLD+'E3-u100_baseline',   '/work/vcomp/exp/deepfirst_20260915/u100-deep_f2load', 1048576000, 24, 1000),
       ('LZ4',        OLD+'E7-lz4_baseline',    NB+'E7-lz4_f2load',    1048576000, 24, 1000),
       ('256MB x4',   OLD+'E4-256m4_baseline',  NB+'E4-256m4_f2load',  1048576000, 24, 1000),
       ('1024MB x10', OLD+'E4-1024m10_baseline',NB+'E4-1024m10_f2load',1048576000, 24, 1000),
       ('SST 16 MB',  BM+'E5-sst16_baseline',   BM+'E5-sst16_f2load',  1048576000, 24, 1000),
       ('SST 256 MB', BM+'E5-sst256_baseline',  BM+'E5-sst256_f2load', 1048576000, 24, 1000)]

def state(db, num, key, value):
    out = subprocess.run(['./db_bench', '--benchmarks=coverage', f'--db={db}',
                          '--use_existing_db=1', '--readonly=true', f'--num={num}',
                          f'--key_size={key}', f'--value_size={value}',
                          '--compression_type=none', '--format_version=7', '--bloom_bits=10',
                          '--disable_auto_compactions=true'],
                         capture_output=True, text=True).stdout
    lv = []
    for line in out.splitlines():
        p = line.split()
        if len(p) >= 3 and p[0].isdigit() and p[1].isdigit() and re.match(r'^[\d.]+$', p[2]):
            lv.append((int(p[0]), int(p[1]), float(p[2])))   # level, files, MiB
    if not lv: return None
    files = sum(f for _, f, _ in lv); mib = sum(s for _, _, s in lv)
    return dict(files=files, tib=mib / 1048576, avg=mib / files, levels=len(lv),
                per_level={l: (f, s) for l, f, s in lv})

rows = []
for name, b, f, num, k, v in PTS:
    if not (os.path.exists(b + '/CURRENT') and os.path.exists(f + '/CURRENT')):
        print(f'  {name}: DB 없음'); continue
    B, F = state(b, num, k, v), state(f, num, k, v)
    if not B or not F: print(f'  {name}: 읽기 실패'); continue
    rows.append((name, B, F))

hdr = f"{'설정':15s}|{'DB size (TiB)':^25s}|{'SST count':^25s}|{'avg SST (MiB)':^24s}|{'levels':^9s}"
print(hdr); print('-' * len(hdr))
print(f"{'':15s}|{'base':>8s}{'F2':>8s}{'Δ':>9s}|{'base':>8s}{'F2':>8s}{'Δ':>9s}|{'base':>7s}{'F2':>8s}{'Δ':>9s}| b / f")
d_sz, d_ct, d_avg = [], [], []
for n, B, F in rows:
    ds = F['tib']/B['tib']-1; dc = F['files']/B['files']-1; da = F['avg']/B['avg']-1
    d_sz.append(ds); d_ct.append(dc); d_avg.append(da)
    print(f"{n:15s}|{B['tib']:8.3f}{F['tib']:8.3f}{100*ds:+8.2f}%|{B['files']:8d}{F['files']:8d}{100*dc:+8.2f}%|"
          f"{B['avg']:7.1f}{F['avg']:8.1f}{100*da:+8.2f}%| {B['levels']} / {F['levels']}")
def s(v, unit='%'):
    a = [abs(x) for x in v]
    return (f"최대 {100*max(a):.2f}{unit}, 중앙 |Δ| {100*statistics.median(a):.2f}{unit}, "
            f"평균 |Δ| {100*statistics.mean(a):.2f}{unit}  (부호 포함 {100*min(v):+.2f} ~ {100*max(v):+.2f})")
print(f"\n  DB size    : {s(d_sz)}")
print(f"  SST count  : {s(d_ct)}")
print(f"  avg SST    : {s(d_avg)}")
json.dump([{'config': n, 'baseline': {k: v for k, v in B.items() if k != 'per_level'},
            'f2load': {k: v for k, v in F.items() if k != 'per_level'}} for n, B, F in rows],
          open('experiments/results/structure_stats_260915.json', 'w'), indent=1)
print('\n  saved experiments/results/structure_stats_260915.json')
