#!/usr/bin/env python3
"""Exact-membership plan: coverage and loading cost against the no-bitmap path.

Three configurations on one frozen binary, interleaved per rep so drift lands on
all of them alike:
  nobitmap       --vcomp_exact_membership=false        (the ch.3 F2Load config)
  bitmap_noplan  exact membership, each file spreads its picks evenly
  bitmap_plan    exact membership with the max-flow plan
Inputs: the synthetic default (no trace; exact membership makes Phase 1 keygen
serial here) and the two 1 TB skew traces (trace replay is serial either way).
Each cell loads, records the phase timings and the in-run coverage oracle, and
deletes the DB.
"""
import argparse, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ART = REPO / 'experiments/artifacts'
TRACES = Path('/work/vcomp/load_traces')
CONFIGS = {
    'nobitmap':      dict(vcomp_exact_membership=False),
    'bitmap_noplan': dict(vcomp_exact_membership=True, vcomp_membership_plan=False),
    'bitmap_plan':   dict(vcomp_exact_membership=True, vcomp_membership_plan=True),
}
INPUTS = {
    'synth': None,
    'a0':    TRACES / 'skew_1000gib_24x1000_a0_s12345678.vload',
    'a0p99': TRACES / 'skew_1000gib_24x1000_a0p99_s12345678.vload',
}
PAT = {
    'fillvirtual_s': r'fillvirtual\s*:\s*[\d.]+ micros/op .*? ([\d.]+) seconds',
    'phase1_keygen_s': r'Phase 1 breakdown: keygen=([\d.]+)s',
    'phase1_sort_s': r'Phase 1 breakdown: .*?sort=([\d.]+)s',
    'phase1_plr_s': r'Phase 1 breakdown: .*?plr_fit=([\d.]+)s',
    'phase2a_s': r'Phase 2a \(materialize \+ SST write\): ([\d.]+) sec',
    'plan_s': r'membership plan: .*?, ([\d.]+) sec',
    'plan_segments': r'membership plan: \d+ files, (\d+) segments',
    'plan_unplaceable': r'membership plan: .*?unplaceable (\d+)',
    'plan_no_file': r'membership plan: .*?no file covers (\d+)',
    'plan_too_small': r'membership plan: .*?files too small (\d+)',
    'ingested': r'exact membership: ingested key ids (\d+)',
    'covered': r'exact membership: .*?covered (\d+)',
}

def now(): return time.strftime('%Y-%m-%dT%H:%M:%S%z')

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run-id', default='membership_timing')
    ap.add_argument('--reps', type=int, default=3)
    ap.add_argument('--inputs', default='synth,a0,a0p99')
    ap.add_argument('--configs', default='nobitmap,bitmap_noplan,bitmap_plan')
    ap.add_argument('--db-root', default='/work/vcomp/exp/mtiming')
    ap.add_argument('--threads', type=int, default=16)
    a = ap.parse_args()
    out = ART / a.run_id; out.mkdir(parents=True, exist_ok=True)
    frozen = out / 'f2_db_bench'
    if not frozen.exists():
        shutil.copy2(REPO / 'db_bench', frozen)          # never follows later rebuilds
    sha = subprocess.check_output(['sha256sum', str(frozen)], text=True).split()[0]
    (out / 'plan.json').write_text(json.dumps(dict(
        created=now(), binary_sha256=sha, reps=a.reps, inputs=a.inputs, configs=a.configs,
        threads=a.threads, git=subprocess.check_output(
            ['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
        host=dict(online_cpus=os.cpu_count(),
                  kernel_cmdline=Path('/proc/cmdline').read_text().strip())), indent=1))
    rows = out / 'results.jsonl'
    done = set()
    if rows.exists():
        for line in rows.read_text().splitlines():
            r = json.loads(line); done.add((r['rep'], r['input'], r['config']))
    for rep in range(1, a.reps + 1):
        for inp in a.inputs.split(','):
            for cfg in a.configs.split(','):
                if (rep, inp, cfg) in done: continue
                cell = f'r{rep}_{inp}_{cfg}'
                rid = f'{a.run_id}_{cell}'
                (ART / rid / 'bin').mkdir(parents=True, exist_ok=True)
                link = ART / rid / 'bin' / 'f2_db_bench'
                if not link.exists(): link.symlink_to(frozen)
                ov = dict(CONFIGS[cfg], vcomp_global_unique_keys=False,
                          vcomp_global_unique_keys_deep_first=False,
                          max_background_jobs=a.threads, vcomp_materialize_workers=a.threads)
                if INPUTS[inp] is not None: ov['load_trace_file'] = str(INPUTS[inp])
                qf = out / f'{cell}.queue.json'
                qf.write_text(json.dumps([[cell, 'f2load', 1000, 24, 1000, None, ov]]))
                t0 = time.time()
                rc = subprocess.run([sys.executable, str(REPO / 'experiments/scripts/eval/run_eval_campaign.py'),
                                     '--run-id', rid, '--db-root', a.db_root, '--execute',
                                     '--queue-file', str(qf)], cwd=REPO).returncode
                wall = time.time() - t0
                log = ART / rid / f'{cell}_f2load' / 'stdout_stderr.log'
                text = log.read_text(errors='replace') if log.exists() else ''
                r = dict(rep=rep, input=inp, config=cfg, rc=rc, load_wall_s=round(wall, 1),
                         finished_at=now())
                for k, p in PAT.items():
                    mm = re.search(p, text)
                    r[k] = float(mm.group(1)) if mm else None
                if r['ingested'] and r['covered'] is not None:
                    r['coverage_pct'] = round(100 * r['covered'] / r['ingested'], 4)
                with rows.open('a') as f: f.write(json.dumps(r) + '\n')
                print(f'[{now()}] {cell}: wall {wall:.1f}s fillvirtual {r["fillvirtual_s"]} '
                      f'phase2a {r["phase2a_s"]} plan {r["plan_s"]} cov {r.get("coverage_pct")}', flush=True)
                shutil.rmtree(Path(a.db_root) / f'{cell}_f2load', ignore_errors=True)
    print(f'[{now()}] ALL DONE', flush=True)

if __name__ == '__main__':
    main()
