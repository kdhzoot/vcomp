#!/usr/bin/env python3
"""mdump 출력을 재생해 compaction 단위를 복원하고 레벨 구조를 비교한다.

vcomp는 여러 virtual compaction을 하나의 MANIFEST edit으로 배치 커밋하므로,
edit 안을 key-range 연결요소로 쪼개야 compaction 한 건이 나온다.
"""
import sys, collections

def parse(path):
    edits, cur = [], None
    for line in open(path):
        if line.startswith('EDIT'):
            if cur is not None: edits.append(cur)
            cur = {'add': [], 'del': []}
        elif line.startswith('  DEL'):
            _, l, f = line.split(); cur['del'].append((int(l), int(f)))
        elif line.startswith('  ADD'):
            p = line.split()
            cur['add'].append((int(p[1]), int(p[2]), int(p[3]), int(p[4]), int(p[5])))
    if cur is not None: edits.append(cur)
    return edits

def components(items):
    """items: [(lo, hi, tag)] -> key-range가 겹치는 것끼리 묶는다."""
    items = sorted(items)
    comps, cur, ch = [], [], None
    for lo, hi, tag in items:
        if cur and lo > ch:
            comps.append(cur); cur = []
        cur.append((lo, hi, tag)); ch = hi if ch is None or lo > ch else max(ch, hi)
    if cur: comps.append(cur)
    return comps

def cov(files, domain):
    if not files: return 0.0, 0
    r = sorted((lo, hi) for lo, hi, _ in files)
    m, cl, ch = [], r[0][0], r[0][1]
    for a, b in r[1:]:
        if a <= ch: ch = max(ch, b)
        else: m.append((cl, ch)); cl, ch = a, b
    m.append((cl, ch))
    return 100.0 * sum(b - a for a, b in m) / domain, len(m)

def run(path, domain):
    edits = parse(path)
    level = collections.defaultdict(dict)
    home, meta = {}, {}
    jobs = []
    for e in edits:
        items = []
        for lvl, f in e['del']:
            l = home.get(f, lvl); lo, hi, sz = meta.get(f, (0, 0, 0))
            items.append((lo, hi, ('d', l, f, sz)))
        for lvl, f, size, lo, hi in e['add']:
            items.append((lo, hi, ('a', lvl, f, size)))
        for comp in components(items):
            din = collections.Counter(); dbytes = 0
            aout = collections.Counter(); abytes = 0
            for lo, hi, (kind, lvl, f, sz) in comp:
                if kind == 'd': din[lvl] += 1; dbytes += sz
                else: aout[lvl] += 1; abytes += sz
            if not din and not aout: continue
            jobs.append(dict(src=min(din) if din else -1, dst=max(aout) if aout else -1,
                             nin=sum(din.values()), nout=sum(aout.values()),
                             inb=dbytes, outb=abytes,
                             span=max(h for _, h, _ in comp) - min(l for l, _, _ in comp),
                             levels=tuple(sorted(din))))
        for lvl, f in e['del']:
            l = home.pop(f, lvl); level[l].pop(f, None); meta.pop(f, None)
        for lvl, f, size, lo, hi in e['add']:
            level[lvl][f] = (lo, hi, size); home[f] = lvl; meta[f] = (lo, hi, size)
    return level, jobs

def main(domain, paths):
    for p in paths:
        lbl = p.split('/')[-1].replace('md_', '').replace('.txt', '')
        level, jobs = run(p, domain)
        flow = collections.Counter()
        fbytes = collections.Counter()
        for j in jobs:
            if j['src'] < 0: continue
            key = (j['src'], j['dst'], len(j['levels']))
            flow[key] += 1; fbytes[key] += j['inb']
        print(f"\n=== {lbl}")
        print(f"  {'compaction':>16s} {'건수':>7s} {'입력GiB':>10s} {'평균입력수':>10s} {'평균출력수':>10s} {'평균폭':>12s}")
        agg = collections.defaultdict(list)
        for j in jobs:
            if j['src'] < 0: continue
            agg[(j['src'], j['dst'], len(j['levels']))].append(j)
        for key in sorted(agg):
            js = agg[key]
            s, d, nl = key
            tag = f"L{s}->L{d}" + ("(move)" if nl == 1 and d > s else "")
            print(f"  {tag:>16s} {len(js):7d} {sum(x['inb'] for x in js)/2**30:10.2f} "
                  f"{sum(x['nin'] for x in js)/len(js):10.2f} {sum(x['nout'] for x in js)/len(js):10.2f} "
                  f"{sum(x['span'] for x in js)/len(js):12.0f}")
        print(f"  {'lvl':>4s} {'files':>7s} {'dom%':>8s} {'runs':>6s}")
        for l in sorted(level):
            if not level[l]: continue
            c, r = cov(list(level[l].values()), domain)
            print(f"  {l:4d} {len(level[l]):7d} {c:8.2f} {r:6d}")

if __name__ == '__main__':
    main(int(sys.argv[1]), sys.argv[2:])
