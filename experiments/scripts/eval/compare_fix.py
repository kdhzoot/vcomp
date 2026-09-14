#!/usr/bin/env python3
"""점별 YCSB C: baseline vs F2Load(수정 전, 캠페인) vs F2Load(수정 후, fix_20260913)."""
import json, glob, collections, sys
ART = 'experiments/artifacts'
import re
def level_hits(log):
    """positive lookup = memtable/L0/L1/L2+ hit 합, 분모 = number.keys.read (Get 호출 수).
    (results.json의 keys_read는 Get 호출 수라 found 비율이 아니다.)"""
    c = {}
    try:
        for line in open(log, errors='ignore'):
            m = re.match(r'rocksdb\.(memtable\.hit|l0\.hit|l1\.hit|l2andup\.hit|number\.keys\.read) COUNT : (\d+)', line)
            if m: c[m.group(1)] = int(m.group(2))
    except FileNotFoundError:
        return 0, 0
    return sum(v for k, v in c.items() if k != 'number.keys.read'), c.get('number.keys.read', 0)
def load(pattern, tag):
    out = {}
    for f in glob.glob(pattern):
        run = f.split('/')[2]
        if 'superseded' in run: continue
        cell = f.split('/')[3].split('__')[0]
        point, mode = cell.rsplit('_', 1)
        r = json.load(open(f))
        found, nreads = level_hits(f.replace('result.json', 'stdout_stderr.log'))
        ops = nreads or r['ops_per_sec'] * r['elapsed_sec']
        out[(point, mode)] = dict(ops=r['ops_per_sec'], fpr=r['filter_probes_per_read'],
                                  found=100 * found / ops, ssts=r['source_identity_ssts'],
                                  p99=r['p99_us'], sst_rd=r['sst_read_count'] / ops)
    return out
old = load(f'{ART}/eval_*/*__workloadc/result.json', 'old')
new = load(f'{ART}/fix_20260913_ycsbc/*__workloadc/result.json', 'new')
# 2026-09-14 결정: E5-256 baseline은 r2(fix_20260913_ycsbc/E5-256-r2_baseline)를 쓴다. r1(L1 파일 1개 78% 폭, 캐시 핸들 경합)은 폐기.
if ('E5-256-r2', 'baseline') in new:
    old[('E5-256', 'baseline')] = new.pop(('E5-256-r2', 'baseline'))
canon = lambda p: 'P0-r1' if p.startswith('P0fix') else p
points = sorted({p for p, m in new}, key=lambda p: -abs(old.get((canon(p),'f2load'),{}).get('ops',0)/max(old.get((canon(p),'baseline'),{}).get('ops',1),1)-1))
hdr = f"{'point':11s} | {'ops/s  base':>11s} {'old':>8s} {'new':>8s} | {'filter/read base':>16s} {'old':>6s} {'new':>6s} | {'found% b/o/n':>20s} | {'ssts b/o/n':>20s}"
print(hdr); print('-' * len(hdr))
for p in points:
    b = old.get((canon(p), 'baseline')); o = old.get((canon(p), 'f2load')); n = new.get((p, 'f2load'))
    if not b or not n: continue
    pct = lambda x: f"{100*(x['ops']/b['ops']-1):+6.1f}%" if x else '     - '
    fpr = lambda x: f"{x['fpr']:6.2f}" if x else '   -  '
    print(f"{p:11s} | {b['ops']:11.0f} {pct(o):>8s} {pct(n):>8s} | {b['fpr']:16.3f} {fpr(o)} {fpr(n)} | "
          f"{b['found']:6.2f}/{(o or {}).get('found',0):5.2f}/{n['found']:5.2f} | {b['ssts']:6d}/{(o or {}).get('ssts',0):6d}/{n['ssts']:6d}")
