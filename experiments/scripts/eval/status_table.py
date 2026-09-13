#!/usr/bin/env python3
"""실험별로 필요한 DB를 나열하고 로딩 완료 여부를 표시한다.

상태는 캠페인 results.json(실측 시간)과 DB 디렉터리 존재로 판정한다.
"""
import json, math, subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ART = REPO / 'experiments/artifacts'
DBROOT = Path('/work/vcomp/exp/eval_20260909_night1')
N0 = 1048576000

# 예측 모델 (night1 실측 P0 0.936 h, 8 TB 10.394 h 두 점에 맞춤)
REC, BYTE, E = 2.052e-10 * 0.962, 0.729, 1.208
base = lambda gib, n: BYTE * (gib / 1024) ** E + REC * n
FLUSH = 0.262
uniq = lambda u: FLUSH + (0.936 - FLUSH) * (u / 63.2)

# 실험 -> [(표시 point, 변경, baseline 예측, F2Load 예측, 실제 DB 이름, 비고)]
# 각 축의 중심점은 P0-r1 DB를 그대로 쓴다. 중복이지만 축마다 전부 나열한다.
P0 = 'P0-r1'
PLAN = {
    'P0 반복 (noise floor)': [
        ('P0-r1', '기준', 0.936, 0.019, 'P0-r1', ''),
        ('P0-r2', '기준', 0.936, 0.019, 'P0-r2', ''),
        ('P0-r3', '기준', 0.936, 0.019, 'P0-r3', '')],
    'E1 KV size': [
        ('E1-100B', 'KV 100 B', base(98, N0), 0.02, 'E1-100B', ''),
        ('E1-500B', 'KV 500 B', base(488, N0), 0.02, 'E1-500B', ''),
        ('E1-1KB', 'KV 1 KB', 0.936, 0.019, P0, '= P0'),
        ('E1-5KB', 'KV 5 KB', base(5000, N0), 0.04, 'E1-5KB', ''),
        ('E1-10KB', 'KV 10 KB', base(10000, N0), 0.10, 'E1-10KB', '')],
    'E2 Dataset size': [
        ('E2-1T', 'input 1 TB', 0.936, 0.019, P0, '= P0'),
        ('E2-2T', 'input 2 TB', base(2000, 2 * N0), 0.07, 'E2-2T', ''),
        ('E2-4T', 'input 4 TB', base(4000, 4 * N0), 0.21, 'E2-4T', ''),
        ('E2-8T', 'input 8 TB', base(8000, 8 * N0), 0.72, 'E2-8T', '')],
    'E3 Uniqueness': [
        (f'E3-u{u}', f'unique {u}%', uniq(u), 0.02, f'E3-u{u}', '트레이스 경로')
        for u in (25, 50, 75, 100)],
    'E4 Level 설정': [
        ('E4-64m4', 'L1 64 MiB, mult 4', 0.78, 0.02, 'E4-64m4', ''),
        ('E4-64m10', 'L1 64 MiB, mult 10', 1.03, 0.02, 'E4-64m10', ''),
        ('E4-256m4', 'L1 256 MiB, mult 4', 0.70, 0.02, 'E4-256m4', ''),
        ('E4-256m10', 'L1 256 MiB, mult 10', 0.936, 0.019, P0, '= P0'),
        ('E4-1024m4', 'L1 1 GiB, mult 4', 0.64, 0.02, 'E4-1024m4', ''),
        ('E4-1024m10', 'L1 1 GiB, mult 10', 0.84, 0.02, 'E4-1024m10', '')],
    'E5 Memtable/SST': [
        ('E5-16', '16 MiB', 1.08, 0.02, 'E5-16', ''),
        ('E5-64', '64 MiB', 0.936, 0.019, P0, '= P0'),
        ('E5-256', '256 MiB', 0.89, 0.02, 'E5-256', '')],
    'E6 Compaction policy': [
        ('E6-MinOverlappingRatio', 'compaction_pri 3', 0.936, 0.019, P0, '= P0'),
        ('E6-ByCompensatedSize', 'compaction_pri 0', 0.94, 0.02, 'E6-ByCompensatedSize', ''),
        ('E6-OldestLargestSeq', 'compaction_pri 1', 0.94, 0.02, 'E6-OldestLargestSeq', ''),
        ('E6-OldestSmallestSeq', 'compaction_pri 2', 0.94, 0.02, 'E6-OldestSmallestSeq', '')],
    'E7 Compression': [
        ('E7-none', 'none', 0.936, 0.019, P0, '= P0'),
        ('E7-lz4', 'lz4', 1.00, 0.02, 'E7-lz4', ''),
        ('E7-zstd', 'zstd', 1.30, 0.02, 'E7-zstd', '')],
    'E8 SST metadata': [
        ('E8-bloom10', 'bloom_bits 10 (기본)', 0.936, 0.019, P0, '= P0'),
        ('E8-bloom0', 'bloom_bits 0', 0.89, 0.02, 'E8-bloom0', ''),
        ('E8-bloom16', 'bloom_bits 16', 0.98, 0.02, 'E8-bloom16', ''),
        ('E8-partitioned', 'partition_index_and_filters', 0.94, 0.02, 'E8-partitioned', ''),
        ('E8-ribbon', 'use_ribbon_filter', 1.03, 0.02, 'E8-ribbon', '')],
}


def measured():
    out = {}
    for results in ART.glob('eval_*/results.json'):
        for r in json.loads(results.read_text()):
            out[(r['point'], r['mode'])] = r['elapsed_sec'] / 3600
    return out


def running():
    try:
        ps = subprocess.check_output(['ps', '-eo', 'cmd'], text=True)
    except Exception:
        return set()
    return {line.split('--db=')[1].split()[0].rsplit('/', 1)[-1]
            for line in ps.splitlines() if '--db=' in line and 'f2_db_bench' in line}


def main():
    got, run = measured(), running()
    print('| 실험 | point | 변경 | DB | baseline | F2Load |')
    print('|---|---|---|---|---|---|')
    rows = uniq_db = 0
    seen, pending_b, pending_f = set(), 0.0, 0.0
    for exp, points in PLAN.items():
        for i, (pid, change, pb, pf, db, note) in enumerate(points):
            cells = []
            for mode, pred in (('baseline', pb), ('f2load', pf)):
                key = (db, mode)
                if key in got:
                    cells.append(f'완료 **{got[key]:.2f} h**')
                elif f'{db}_{mode}' in run:
                    cells.append('로딩 중')
                elif (DBROOT / f'{db}_{mode}').exists():
                    cells.append('완료')
                else:
                    cells.append(f'대기 ~{pred:.2f} h')
                    if key not in seen:
                        if mode == 'baseline':
                            pending_b += pred
                        else:
                            pending_f += pred
                if key not in seen:
                    seen.add(key)
                    uniq_db += 1
            rows += 1
            print(f'| {exp if i == 0 else ""} | {pid} | {change}'
                  f'{" (" + note + ")" if note else ""} | `{db}` | {cells[0]} | {cells[1]} |')
    done = sum(1 for (db, m) in seen
               if (db, m) in got or ((DBROOT / f'{db}_{m}').exists()
                                     and f'{db}_{m}' not in run))
    print(f'\n행 {rows}개 = 축별로 세어 중복 포함. 실제 DB는 {uniq_db}개이고 그중 {done}개 완료.')
    print(f'남은 로딩 baseline {pending_b:.1f} h + F2Load {pending_f:.1f} h.')
    print('E9 virtual compaction accuracy는 별도 DB 없이 baseline 로딩에 캡처를 얹는다.')


if __name__ == '__main__':
    main()
