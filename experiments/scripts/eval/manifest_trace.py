#!/usr/bin/env python3
"""MANIFEST를 재생해 compaction trace를 복원하고 레벨별 coverage 궤적을 뽑는다.

ldb manifest_dump 출력을 파싱한다. 각 VersionEdit의 AddFile/DeleteFile을
순서대로 적용하면서, 편집마다 (삭제 레벨 -> 추가 레벨) = compaction 한 건으로 보고
그 시점의 레벨별 key-space coverage를 기록한다.
"""
import re, sys, json, collections

ADD = re.compile(r"AddFile:\s*(\d+)\s+(\d+)\s+(\d+)\s+'([0-9a-fA-F]*)'\s*seq:\d+,\s*type:\d+\s+'([0-9a-fA-F]*)'")
DEL = re.compile(r"DeleteFile:\s*(\d+)\s+(\d+)")

def hexkey(h):
    # db_bench key: 8-byte big-endian id prefix, hex-encoded in manifest_dump
    return int(h[:16], 16) if len(h) >= 16 else 0

def parse(path):
    """returns list of edits: {'add':[(lvl,fnum,size,lo,hi)], 'del':[(lvl,fnum)]}"""
    edits, cur = [], None
    for line in open(path, errors='ignore'):
        if line.startswith('--------------- Column family') or line.startswith('EditNumber'):
            if cur and (cur['add'] or cur['del']):
                edits.append(cur)
            cur = {'add': [], 'del': []}
            continue
        if cur is None:
            cur = {'add': [], 'del': []}
        m = ADD.search(line)
        if m:
            lvl, fnum, size, lo, hi = m.groups()
            cur['add'].append((int(lvl), int(fnum), int(size), hexkey(lo), hexkey(hi)))
            continue
        m = DEL.search(line)
        if m:
            cur['del'].append((int(m.group(1)), int(m.group(2))))
    if cur and (cur['add'] or cur['del']):
        edits.append(cur)
    return edits

def coverage(files_at_level):
    if not files_at_level:
        return 0.0, 0, 0
    r = sorted((lo, hi) for lo, hi, _ in files_at_level)
    merged, cl, ch = [], r[0][0], r[0][1]
    for a, b in r[1:]:
        if a <= ch: ch = max(ch, b)
        else: merged.append((cl, ch)); cl, ch = a, b
    merged.append((cl, ch))
    union = sum(b - a for a, b in merged)
    extent = merged[-1][1] - merged[0][0]
    return (100.0 * union / extent if extent else 100.0), len(merged), len(r)

def main(path, label):
    edits = parse(path)
    level = collections.defaultdict(dict)   # lvl -> fnum -> (lo,hi,size)
    home = {}                                # fnum -> lvl
    events = []
    for e in edits:
        dels = collections.Counter()
        for lvl, fnum in e['del']:
            lvl = home.pop(fnum, lvl)
            level[lvl].pop(fnum, None)
            dels[lvl] += 1
        adds = collections.Counter()
        for lvl, fnum, size, lo, hi in e['add']:
            level[lvl][fnum] = (lo, hi, size)
            home[fnum] = lvl
            adds[lvl] += 1
        if not dels and not adds:
            continue
        kind = 'flush' if not dels else ('move' if len(adds) == 1 and len(dels) == 1
                                         and sum(adds.values()) == sum(dels.values()) else 'compact')
        src = min(dels) if dels else -1
        dst = max(adds) if adds else -1
        events.append(dict(src=src, dst=dst, nin=sum(dels.values()),
                           nout=sum(adds.values()), kind=kind))
    print(f"=== {label}: {len(events)} edits")
    byp = collections.Counter()
    for ev in events:
        byp[(ev['src'], ev['dst'])] += 1
    for (s, d), n in sorted(byp.items()):
        print(f"    L{s}->L{d}: {n}")
    print("    final per-level:")
    for lvl in sorted(level):
        if not level[lvl]: continue
        fs = [(lo, hi, sz) for lo, hi, sz in level[lvl].values()]
        cov, nruns, nf = coverage(fs)
        print(f"      L{lvl}: files={nf:5d} size={sum(s for _,_,s in fs)/2**30:8.2f} GiB "
              f"cov={cov:6.2f}% runs={nruns}")
    return events, level

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else sys.argv[1])
