#!/usr/bin/env python3
"""레벨별 compaction이 key 공간을 훑는 '순서'를 비교한다.

baseline은 커서(next_compaction_index)를 따라 key 순서대로 쓸고 지나가고,
그 결과 남은 파일들이 인접해 coverage가 깊이에 따라 단조 증가한다.
vcomp가 같은 순서를 재현하는지 본다.
"""
import sys, collections
sys.path.insert(0, 'experiments/scripts/eval')
from replay_manifest import parse, components

def jobs_of(path):
    edits = parse(path)
    home, meta = {}, {}
    out = []
    for e in edits:
        items = []
        for lvl, f in e['del']:
            l = home.get(f, lvl); lo, hi, sz = meta.get(f, (0, 0, 0))
            items.append((lo, hi, ('d', l, f, sz)))
        for lvl, f, size, lo, hi in e['add']:
            items.append((lo, hi, ('a', lvl, f, size)))
        for comp in components(items):
            din = collections.Counter()
            for lo, hi, (k, lvl, f, sz) in comp:
                if k == 'd': din[lvl] += 1
            if not din: continue
            lo = min(l for l, _, _ in comp); hi = max(h for _, h, _ in comp)
            out.append((min(din), (lo + hi) // 2, hi - lo))
        for lvl, f in e['del']:
            l = home.pop(f, lvl); meta.pop(f, None)
        for lvl, f, size, lo, hi in e['add']:
            home[f] = lvl; meta[f] = (lo, hi, size)
    return out

def main(domain, paths):
    print(f"  {'run':30s} {'src':>4s} {'jobs':>6s} {'평균|Δ위치|/도메인':>18s} {'전진비율':>9s}")
    for p in paths:
        lbl = p.split('/')[-1].replace('md_', '').replace('.txt', '')
        js = jobs_of(p)
        for src in sorted({s for s, _, _ in js}):
            mids = [m for s, m, _ in js if s == src]
            if len(mids) < 10 or src < 1: continue
            d = [mids[i+1] - mids[i] for i in range(len(mids)-1)]
            fwd = sum(1 for x in d if x > 0) / len(d)
            print(f"  {lbl:30s} {src:4d} {len(mids):6d} "
                  f"{sum(abs(x) for x in d)/len(d)/domain:18.4f} {fwd:9.3f}")
        print()

main(int(sys.argv[1]), sys.argv[2:])
