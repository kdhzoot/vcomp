#!/usr/bin/env python3
"""PERFILE 덤프에서 레벨별 key-space 갭 구조를 분석한다."""
import sys, collections

def load(path):
    lv = collections.defaultdict(list)
    for line in open(path, errors='ignore'):
        if line.startswith('PERFILE '):
            _, l, a, b = line.split()
            lv[int(l)].append((int(a), int(b)))
    return lv

def analyze(ranges, domain):
    ranges = sorted(ranges)
    lo, hi = ranges[0][0], ranges[-1][1]
    merged = []
    cl, ch = ranges[0]
    for a, b in ranges[1:]:
        if a <= ch: ch = max(ch, b)
        else: merged.append((cl, ch)); cl, ch = a, b
    merged.append((cl, ch))
    gaps = [(merged[i+1][0]-merged[i][1]) for i in range(len(merged)-1)]
    gaps.sort(reverse=True)
    union = sum(b-a for a, b in merged)
    return dict(files=len(ranges), lo=lo, hi=hi, extent=hi-lo, union=union,
                cov=100.0*union/max(hi-lo, 1), dom_cov=100.0*union/domain,
                ngaps=len(gaps), gap_sum=sum(gaps),
                top=gaps[:3], med=gaps[len(gaps)//2] if gaps else 0)

def main(files, domain):
    per = {f: load(f) for f in files}
    levels = sorted({l for d in per.values() for l in d})
    for l in levels:
        print(f"--- L{l}")
        print(f"    {'run':28s} {'files':>6s} {'cov%':>7s} {'dom%':>7s} {'gaps':>6s} "
              f"{'gapsum':>12s} {'medgap':>10s}  top3 gaps")
        for f in files:
            if l not in per[f]: continue
            s = analyze(per[f][l], domain)
            name = f.split('/')[-1].replace('cov_', '').replace('.txt', '')
            print(f"    {name:28s} {s['files']:6d} {s['cov']:7.2f} {s['dom_cov']:7.2f} "
                  f"{s['ngaps']:6d} {s['gap_sum']:12d} {s['med']:10d}  {s['top']}")

if __name__ == '__main__':
    main(sys.argv[2:], int(sys.argv[1]))
