#!/usr/bin/env python3
"""SSD read-path probe: YCSB C, uniform requests, no block cache, 60 s per DB.

With the block cache at one byte every data and filter read goes to the device,
so the per-cell iostat trace of md0 shows what each DB's reads cost the SSD.
The question is whether the older baseline DBs (loaded 5-10 September) read
slower than the F2Load DBs loaded on the 13th, which would make throughput
differ even where the logical metrics match.

One row per DB: throughput and mean operation latency from db_bench, md0 read
request rate, bytes and mean request latency (r_await) averaged over the iostat
samples inside the measurement window, and the same counts from the diskstats
snapshots taken at cell start and end.
"""
import csv
import glob
import json
from pathlib import Path
import re
import statistics as st
import time

EXP = Path(__file__).resolve().parents[1]
RESULTS = EXP / 'results'
DEVICE = 'md0'
# md0 is a RAID0 over these; the md layer reports no request latency, so
# r_await and the diskstats time-in-read come from the members.
MEMBERS = {'nvme%dn1' % i for i in range(1, 32)}
BASE_ARMS = {13483: 'n01', 13480: 'n02', 13549: 'n03', 13509: 'n04', 13497: 'r01',
             13522: 'r02', 13461: 'run3', 13460: 'b01', 13496: 'b02', 13520: 'b03'}


def load_dates():
    out = {}
    for p in glob.glob(str(EXP / 'artifacts/log_loads/**/validated.json'), recursive=True):
        d = json.load(open(p))
        if d.get('dataset_gib') != 1000 or d.get('key_bytes') != 24:
            continue
        if d.get('system') == 'baseline' and d['final_sst_count'] in BASE_ARMS:
            arm = BASE_ARMS[d['final_sst_count']]
        elif d.get('system') == 'f2load' and str(d.get('arm', '')).startswith('e'):
            arm = d['arm']
        else:
            continue
        db = Path(d['db_dir'])
        out[arm] = time.strftime('%Y-%m-%d %H:%M', time.localtime(db.stat().st_mtime)) if db.exists() else ''
    return out


def iostat(path):
    """Per iostat sample: read requests/s and rkB/s summed over the RAID members,
    and r_await averaged over the members weighted by their read rate. The
    first sample (cumulative since boot) is dropped, and only samples with real
    read traffic count as the measurement window."""
    samples, cur = [], {}
    for line in open(path):
        f = line.split()
        if not f:
            continue
        if f[0] == 'Device' and cur:
            samples.append(cur)
            cur = {}
        elif f[0] in MEMBERS and len(f) >= 8:
            cur[f[0]] = (float(f[1]), float(f[2]), float(f[5]), float(f[7]))
    if cur:
        samples.append(cur)
    agg = []
    for smp in samples[1:]:
        rps = sum(v[0] for v in smp.values())
        if rps <= 0:
            continue
        agg.append((rps, sum(v[1] for v in smp.values()),
                    sum(v[0] * v[2] for v in smp.values()) / rps, sum(v[3] for v in smp.values())))
    busy = [a for a in agg if a[0] > 1000] or agg
    if not busy:
        return None
    waits = sorted(a[2] for a in busy)
    return dict(iostat_samples=len(busy),
                io_read_rps=round(st.mean(a[0] for a in busy), 1),
                io_read_mbps=round(st.mean(a[1] for a in busy) / 1024, 1),
                io_r_await_ms=round(sum(a[0] * a[2] for a in busy) / sum(a[0] for a in busy), 4),
                io_r_await_p90_ms=round(waits[int(0.9 * (len(waits) - 1))], 4),
                io_write_wps=round(st.mean(a[3] for a in busy), 1))


def diskstats(path):
    """Reads completed, bytes read, ms spent reading, writes completed, bytes
    written, summed over the RAID members (md0 itself reports zero time)."""
    tot = [0, 0, 0, 0, 0]
    for line in open(path):
        f = line.split()
        if len(f) >= 14 and f[2] in MEMBERS:
            for i, v in enumerate((int(f[3]), int(f[5]) * 512, int(f[6]), int(f[7]), int(f[9]) * 512)):
                tot[i] += v
    return tuple(tot) if tot[0] else None


def main():
    dates = load_dates()
    rows = []
    for d in sorted(glob.glob(str(RESULTS / 'ssdprobe_cu_*_260913'))):
        p = Path(d) / 'results.json'
        if not p.exists():
            continue
        arm = Path(d).name[len('ssdprobe_cu_'):-len('_260913')]
        for r in json.load(open(p)):
            if r['phase'] != 'full' or r.get('status') not in ('ok', 'duration_overrun'):
                continue
            ops = r['operations']
            row = dict(system=r['system'], arm=arm, db_loaded=dates.get(arm, ''),
                       status=r['status'], measured_sec=r['measured_seconds'], operations=ops,
                       throughput_ops_sec=round(r['throughput_ops_sec'], 1),
                       avg_latency_us=round(r['avg_latency_us'], 3),
                       positive_lookup_pct=round(100 * r['successful_gets'] / r['engine_keys_read'], 3),
                       filter_checks_per_lookup=round(r['filter_cache_accesses'] / ops, 4),
                       data_cache_hit_fraction=round(r['data_cache_hit_fraction'], 4))
            raw = Path(r['log_dir']) / 'raw'
            raw = raw if raw.is_absolute() else EXP / raw
            io = iostat(raw / 'iostat.log') if (raw / 'iostat.log').exists() else None
            if io:
                row.update(io)
            s, e = diskstats(raw / 'diskstats.start'), diskstats(raw / 'diskstats.end')
            if s and e:
                rc, rb, rms, wc, wb = (e[i] - s[i] for i in range(5))
                row.update(dev_read_count=rc, dev_read_bytes=rb, dev_read_bytes_per_op=round(rb / ops, 1),
                           dev_read_ms_per_req=round(rms / rc, 4) if rc else '',
                           dev_write_count=wc, dev_write_bytes=wb)
            rows.append(row)
    if not rows:
        print('no probe results yet')
        return 0
    fields = list(rows[0].keys())
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    out = RESULTS / 'paper_ssdprobe_cu_260913.tsv'
    with open(out, 'w', newline='') as f:
        w = csv.DictWriter(f, fields, delimiter='\t', extrasaction='ignore')
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: (r['db_loaded'], r['arm'])))
    print('wrote', out, '(%d DBs)' % len(rows))
    print(f"{'arm':5s} {'system':8s} {'loaded':16s} {'tput':>10s} {'lat_us':>7s} {'r_await':>8s} {'p90':>7s} {'read/s':>9s} {'MB/s':>7s} {'B/op':>7s} {'pos%':>6s}")
    for r in sorted(rows, key=lambda r: (r['db_loaded'], r['arm'])):
        print(f"{r['arm']:5s} {r['system']:8s} {r['db_loaded']:16s} {r['throughput_ops_sec']:>10,.0f} {r['avg_latency_us']:>7.1f} "
              f"{r.get('io_r_await_ms', float('nan')):>8.3f} {r.get('io_r_await_p90_ms', float('nan')):>7.3f} {r.get('io_read_rps', float('nan')):>9,.0f} "
              f"{r.get('io_read_mbps', float('nan')):>7.0f} {r.get('dev_read_bytes_per_op', float('nan')):>7.0f} {r['positive_lookup_pct']:>6.2f}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
