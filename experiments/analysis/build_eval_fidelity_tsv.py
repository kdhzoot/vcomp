#!/usr/bin/env python3
"""Section 5.2 (Fidelity) data, as the experiment plan lists it.

Ten independent baseline loadings against ten independent F2Load loadings of
the default 1 TB configuration, each measured on YCSB A-F from a fresh
hard-link clone (48 threads, 50 GiB block cache, 300 s).

  paper_eval_fidelity_loads.tsv    one row per loading: time, final DB bytes and
                                   SST count, F2Load key-id coverage
  paper_eval_fidelity_levels.tsv   one row per loading and level: SST count
                                   and bytes (the per-level state comparison)
  paper_eval_fidelity_behavior.tsv one row per loading and workload: throughput,
                                   mean operation latency, filter checks per
                                   lookup, positive lookups, compaction write
                                   bytes (total and per operation)
  paper_eval_fidelity_device.tsv   one row per loading and workload: md0 read
                                   and write request counts and bytes over the
                                   measurement window, from /proc/diskstats
                                   snapshots taken at cell start and end
  paper_eval_fidelity_summary.tsv  one row per workload and metric: each
                                   system's n, mean, min, max, spread, the mean
                                   difference, and whether the F2Load mean
                                   falls inside the baseline range

Point-lookup metrics are blank for workload E (scan only); compaction write is
blank for the read-only workloads C, D, E.
"""
import csv
import glob
import json
from pathlib import Path
import re
import statistics as st

EXP = Path(__file__).resolve().parents[1]
RESULTS = EXP / 'results'
LOADS = EXP / 'artifacts/log_loads'
RUNS = EXP / 'artifacts/log_runs'
LETTERS = list('ABCDEF') + ['Cu', 'MG']   # Cu: YCSB C uniform, MG: mixgraph (deep copies)
NO_LOOKUP = {'E'}
NO_COMPACTION = {'C', 'D', 'E', 'Cu'}
DEVICE = 'md0'
BASE_ARMS = {13483: 'n01', 13480: 'n02', 13549: 'n03', 13509: 'n04', 13497: 'r01',
             13522: 'r02', 13461: 'run3', 13460: 'b01', 13496: 'b02', 13520: 'b03'}
BEHAVIOR = ['throughput_ops_sec', 'avg_latency_us', 'filter_checks_per_lookup',
            'positive_lookup_pct', 'compaction_write_bytes', 'compaction_write_per_op_bytes']
DEVICE_METRICS = ['device_read_count', 'device_read_bytes', 'device_write_count',
                  'device_write_bytes', 'device_read_bytes_per_op', 'device_write_bytes_per_op']


def loads():
    out, levels = {}, []
    for p in glob.glob(str(LOADS / '**/validated.json'), recursive=True):
        d = json.load(open(p))
        if d.get('dataset_gib') != 1000 or d.get('key_bytes') != 24:
            continue
        if d.get('system') == 'baseline' and d['final_sst_count'] in BASE_ARMS:
            system, arm, cov = 'baseline', BASE_ARMS[d['final_sst_count']], ''
        elif d.get('system') == 'f2load' and str(d.get('arm', '')).startswith('e'):
            system, arm = 'f2load', d['arm']
            bench = Path(d['log_dir']) / 'bench.out'
            bench = bench if bench.is_absolute() else EXP / bench
            m = re.search(r'covered \d+ \(([\d.]+)%\)', bench.read_text())
            cov = float(m.group(1)) if m else ''
        else:
            continue
        out[(system, arm)] = dict(system=system, arm=arm, load_sec=round(d['elapsed_sec'], 1),
                                  final_sst_count=d['final_sst_count'],
                                  final_sst_bytes=d['final_sst_bytes'], coverage_pct=cov)
        for lvl, v in sorted(d['levels'].items(), key=lambda kv: int(kv[0])):
            levels.append(dict(system=system, arm=arm, level=int(lvl), files=v['files'],
                               size_bytes=int(v['size_mib']) * 1024 * 1024))
    return out, levels


def diskstats(path):
    for line in open(path):
        f = line.split()
        if len(f) >= 14 and f[2] == DEVICE:
            # reads completed, sectors read, writes completed, sectors written
            return int(f[3]), int(f[5]) * 512, int(f[7]), int(f[9]) * 512
    return None


def cells():
    out = []
    groups = [('ycsb_band_*', 'baseline', None), ('ycsb_f2band_e*', 'f2load', None),
              ('dc_cu_*', None, 'Cu'), ('dc_mg_*', None, 'MG')]
    for pattern, only_system, forced_workload in groups:
        for d in sorted(glob.glob(str(RESULTS / pattern))):
            p = Path(d) / 'results.json'
            arm = Path(d).name.split('_')[-2]
            if not p.exists() or (only_system == 'baseline' and arm == 'f2'):
                continue
            for r in json.load(open(p)):
                if r['phase'] != 'full' or r.get('status') != 'ok':
                    continue
                if only_system and r['system'] != only_system:
                    continue
                system = r['system']
                w, ops = forced_workload or r['workload'][-1].upper(), r['operations']
                row = dict(system=system, arm=arm, workload=w, operations=ops,
                           duration_sec=r['duration_sec'],
                           throughput_ops_sec=round(r['throughput_ops_sec'], 1),
                           avg_latency_us=round(r['avg_latency_us'], 3),
                           filter_checks_per_lookup=(None if w in NO_LOOKUP else
                                                     round(r['filter_cache_accesses'] / ops, 4)),
                           positive_lookup_pct=(None if w in NO_LOOKUP else
                                                round(100 * r['successful_gets'] / r['engine_keys_read'], 3)),
                           compaction_write_bytes=(None if w in NO_COMPACTION else r['compaction_write_bytes']),
                           compaction_write_per_op_bytes=(None if w in NO_COMPACTION else
                                                          round(r['compaction_write_bytes'] / ops, 1)))
                raw = Path(r['log_dir']) / 'raw'
                raw = raw if raw.is_absolute() else EXP / raw
                s, e = diskstats(raw / 'diskstats.start'), diskstats(raw / 'diskstats.end')
                if s and e:
                    rc, rb, wc, wb = (e[i] - s[i] for i in range(4))
                    row.update(device_read_count=rc, device_read_bytes=rb, device_write_count=wc,
                               device_write_bytes=wb, device_read_bytes_per_op=round(rb / ops, 1),
                               device_write_bytes_per_op=round(wb / ops, 1))
                else:
                    row.update({k: None for k in DEVICE_METRICS})
                out.append(row)
    return out


def write(name, fields, rows):
    with open(RESULTS / name, 'w', newline='') as f:
        w = csv.DictWriter(f, fields, delimiter='\t', extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    print('wrote', RESULTS / name, '(%d rows)' % len(rows))


def main():
    ld, levels = loads()
    data = cells()
    key = lambda r: (r['workload'], r['system'], r['arm'])
    write('paper_eval_fidelity_loads.tsv',
          ['system', 'arm', 'load_sec', 'final_sst_count', 'final_sst_bytes', 'coverage_pct'],
          sorted(ld.values(), key=lambda r: (r['system'], r['arm'])))
    write('paper_eval_fidelity_levels.tsv', ['system', 'arm', 'level', 'files', 'size_bytes'],
          sorted(levels, key=lambda r: (r['system'], r['arm'], r['level'])))
    write('paper_eval_fidelity_behavior.tsv',
          ['system', 'arm', 'workload', 'operations', 'duration_sec'] + BEHAVIOR, sorted(data, key=key))
    write('paper_eval_fidelity_device.tsv',
          ['system', 'arm', 'workload', 'operations', 'duration_sec'] + DEVICE_METRICS, sorted(data, key=key))

    rows = []
    for letter in LETTERS:
        for m in BEHAVIOR + DEVICE_METRICS:
            b = [r[m] for r in data if r['workload'] == letter and r['system'] == 'baseline' and r.get(m) is not None]
            e = [r[m] for r in data if r['workload'] == letter and r['system'] == 'f2load' and r.get(m) is not None]
            if not b or not e:
                continue
            bm, em = st.mean(b), st.mean(e)
            rows.append(dict(workload=letter, metric=m,
                             baseline_n=len(b), baseline_mean=round(bm, 4), baseline_min=min(b), baseline_max=max(b),
                             baseline_spread_pct=round(100 * (max(b) - min(b)) / bm, 2) if bm else '',
                             f2load_n=len(e), f2load_mean=round(em, 4), f2load_min=min(e), f2load_max=max(e),
                             f2load_spread_pct=round(100 * (max(e) - min(e)) / em, 2) if em else '',
                             mean_diff_pct=round(100 * (em - bm) / bm, 3) if bm else '',
                             f2load_mean_in_baseline_range='yes' if min(b) <= em <= max(b) else 'no'))
    write('paper_eval_fidelity_summary.tsv', list(rows[0].keys()), rows)
    missing = sum(1 for r in data if r.get('device_read_bytes') is None)
    print('arms:', {s: len({r['arm'] for r in data if r['system'] == s}) for s in ('baseline', 'f2load')},
          '| cells without diskstats:', missing)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
