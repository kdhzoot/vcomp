#!/usr/bin/env python3
"""Section 5.1 data: loading-time breakdown, flush vs vSST preparation, real vs
virtual compaction, for the 1000 GiB 1 KB and 91 B loadings.

F2Load side: the job-profile captures of 13 September (one loading each, the
vcomp-prof-job fork with VCOMP_JOB_PROFILE). Baseline side: the per-job flush
and compaction breakdowns recorded on 3 September for the Figure 2 four-cell
matrix (flush-only for flush jobs, conventional for compaction jobs).

  paper_eval_loading_breakdown.tsv       sequential fillvirtual phases, the
                                         completion protocol, process wall, and
                                         the concurrent per-kind cumulative times
  paper_eval_flush_vs_prepare.tsv        per-job real flush categories against
                                         per-batch vSST preparation stages and
                                         per-file registration
  paper_eval_compaction_vs_virtual.tsv   per (start level -> output level):
                                         jobs, total, mean, median, input size,
                                         for real compaction, virtual compaction
                                         and virtual trivial moves
  paper_eval_compaction_jobs_raw.tsv     one row per job for scatter plots

Caveats carried into the files: both sides are single loadings; the real
compaction rows include 0->0 intra-L0 jobs, which the virtual picker does not
issue; virtual job rows exclude the shared commit batches, which are reported
once in the breakdown file as their own kind.
"""
import csv
import statistics as st
from pathlib import Path

EXP = Path(__file__).resolve().parents[1]
RESULTS = EXP / 'results'
PROF = Path('/home/smrc/virtual_compaction/vcomp-prof-job/experiments')
F2 = {
    '1KB': dict(run=PROF / 'artifacts/job_profile/full_1000gib_260913_01/r1_on',
                summary=PROF / 'results/job_profile_1000gib_260913'),
    '91B': dict(run=PROF / 'artifacts/job_profile/full_1000gib_91b_260913_01/r1_on',
                summary=PROF / 'results/job_profile_1000gib_91b_260913'),
}
REAL = {
    '1KB': dict(flush=EXP / 'artifacts/log_loads/figure2_1tb_fourcell_260903_resume1/order4_kv1024_nocomp/raw/flush_breakdown.tsv',
                compaction=EXP / 'artifacts/log_loads/figure2_1tb_fourcell_260903_run1/order1_kv1024_conventional/raw/compaction_breakdown.tsv'),
    '91B': dict(flush=EXP / 'artifacts/log_loads/figure2_1tb_fourcell_260903_resume1/order2_kv91_nocomp/raw/flush_breakdown.tsv',
                compaction=EXP / 'artifacts/log_loads/figure2_1tb_fourcell_260903_resume1/order3_kv91_conventional/raw/compaction_breakdown.tsv'),
}
WORKERS = dict(prepare=8, virtual_compaction=48, virtual_trivial_move=48, commit=48, materialize=48,
               key_input=1, registration=1)


def tsv(path):
    with open(path, newline='') as f:
        return list(csv.DictReader(f, delimiter='\t'))


def write(name, fields, rows):
    with open(RESULTS / name, 'w', newline='') as f:
        w = csv.DictWriter(f, fields, delimiter='\t', extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    print('wrote', RESULTS / name, '(%d rows)' % len(rows))


def q(values, p):
    s = sorted(values)
    return s[min(len(s) - 1, int(p * (len(s) - 1)))]


def breakdown():
    rows = []
    for kv, paths in F2.items():
        for r in tsv(paths['summary'] / 'phases.tsv'):
            scope = {'phase': 'sequential', 'load': 'total', 'completion': 'completion',
                     'completion_stage': 'completion_detail', 'process': 'process'}[r['kind']]
            rows.append(dict(kv_size=kv, system='f2load', scope=scope, kind=r['kind'], stage=r['stage'],
                             seconds=round(float(r['elapsed_sec']), 4), count=1, mean_ms='', threads='',
                             note='wall clock; sequential phases sum to load/fillvirtual'))
        for r in tsv(paths['summary'] / 'job_times.tsv'):
            if r['kind'] in ('prepare', 'virtual_compaction', 'virtual_trivial_move', 'commit',
                             'materialize', 'key_input', 'registration'):
                if r['kind'] == 'prepare' and r['stage'] != 'total':
                    continue
                if r['kind'] in ('virtual_compaction', 'virtual_trivial_move') and r['stage'] != 'job_total':
                    continue
                if r['kind'] == 'commit' and r['stage'] != 'batch_total':
                    continue
                if r['kind'] == 'registration' and r['stage'] != 'refill_batch':
                    continue
                if r['kind'] == 'key_input' and r['stage'] != 'stream_generate':
                    continue
                rows.append(dict(kv_size=kv, system='f2load', scope='concurrent', kind=r['kind'], stage=r['stage'],
                                 seconds=round(float(r['inclusive_cumulative_sec']), 4), count=int(r['count']),
                                 mean_ms=round(float(r['mean_ms']), 4), threads=WORKERS[r['kind']],
                                 note='cumulative over concurrent workers; overlaps the sequential phases; '
                                      'levels %s->%s' % (r['start_level'], r['output_level'])
                                      if r['start_level'] != '-1' else 'cumulative over concurrent workers; overlaps the sequential phases'))
    write('paper_eval_loading_breakdown.tsv',
          ['kv_size', 'system', 'scope', 'kind', 'stage', 'seconds', 'count', 'mean_ms', 'threads', 'note'], rows)


def flush_vs_prepare():
    rows = []
    for kv in F2:
        fl = tsv(REAL[kv]['flush'])
        n = len(fl)
        cats = [('sort', 'sort_us'), ('sst_build', 'sst_build_us'), ('write', 'write_us'), ('other', 'other_us')]
        totals = {c: sum(int(r[k]) for r in fl) / 1e6 for c, k in cats}
        per_job = [int(r['total_tracked_us']) / 1e3 for r in fl]
        for c, _ in cats:
            rows.append(dict(kv_size=kv, system='baseline', unit='flush job', jobs=n, category=c,
                             total_sec=round(totals[c], 3), mean_ms=round(1e3 * totals[c] / n, 4),
                             share=round(totals[c] / sum(totals.values()), 4),
                             mean_input_bytes=round(st.mean(int(r['input_bytes']) for r in fl)),
                             mean_input_entries=round(st.mean(int(r['input_entries']) for r in fl)),
                             source=str(REAL[kv]['flush'].relative_to(EXP))))
        rows.append(dict(kv_size=kv, system='baseline', unit='flush job', jobs=n, category='total',
                         total_sec=round(sum(totals.values()), 3), mean_ms=round(st.mean(per_job), 4),
                         median_ms=round(st.median(per_job), 4), p90_ms=round(q(per_job, 0.9), 4), share=1.0,
                         mean_input_bytes=round(st.mean(int(r['input_bytes']) for r in fl)),
                         mean_input_entries=round(st.mean(int(r['input_entries']) for r in fl)),
                         source=str(REAL[kv]['flush'].relative_to(EXP))))
        jt = {(r['kind'], r['stage']): r for r in tsv(F2[kv]['summary'] / 'job_times.tsv')}
        batches = int(jt[('prepare', 'total')]['count'])
        stages = [('sort_dedup_membership', 'sort+dedup+bitmap'), ('kmv_descriptor_certify', 'kmv sketch'),
                  ('plr', 'plr model')]
        prep_total = float(jt[('prepare', 'total')]['inclusive_cumulative_sec'])
        for key, label in stages:
            r = jt[('prepare_stage', key)]
            rows.append(dict(kv_size=kv, system='f2load', unit='vSST batch', jobs=batches, category=label,
                             total_sec=round(float(r['inclusive_cumulative_sec']), 3), mean_ms=round(float(r['mean_ms']), 4),
                             share=round(float(r['inclusive_cumulative_sec']) / prep_total, 4),
                             source=str(F2[kv]['summary'])))
        reg = jt[('registration', 'refill_batch')]
        rows.append(dict(kv_size=kv, system='f2load', unit='vSST batch', jobs=batches, category='registration (per file share)',
                         total_sec=round(float(reg['inclusive_cumulative_sec']), 3),
                         mean_ms=round(1e3 * float(reg['inclusive_cumulative_sec']) / batches, 4),
                         share='', note='%s refill batches, LogAndApply included; divided by prepared files' % reg['count'],
                         source=str(F2[kv]['summary'])))
        pr = jt[('prepare', 'total')]
        per = [float(pr['mean_ms'])]
        rows.append(dict(kv_size=kv, system='f2load', unit='vSST batch', jobs=batches, category='total (prepare)',
                         total_sec=round(prep_total, 3), mean_ms=round(float(pr['mean_ms']), 4),
                         median_ms=round(float(pr['median_ms']), 4), share=1.0,
                         note='sort+dedup+bitmap, KMV, PLR; excludes registration and key generation',
                         source=str(F2[kv]['summary'])))
        kg = jt[('key_input', 'stream_generate')]
        rows.append(dict(kv_size=kv, system='f2load', unit='vSST batch', jobs=int(kg['count']), category='key generation (producer thread)',
                         total_sec=round(float(kg['inclusive_cumulative_sec']), 3), mean_ms=round(float(kg['mean_ms']), 4),
                         share='', note='exact-membership stream; the baseline generates keys in its writer, outside flush jobs',
                         source=str(F2[kv]['summary'])))
    write('paper_eval_flush_vs_prepare.tsv',
          ['kv_size', 'system', 'unit', 'jobs', 'category', 'total_sec', 'mean_ms', 'median_ms', 'p90_ms', 'share',
           'mean_input_bytes', 'mean_input_entries', 'note', 'source'], rows)


def compaction():
    levels, raw = [], []
    for kv in F2:
        real = tsv(REAL[kv]['compaction'])
        groups = {}
        for r in real:
            key = (int(r['start_level']), int(r['output_level']))
            groups.setdefault(key, []).append((int(r['total_tracked_us']) / 1e3, int(r['input_bytes']),
                                               int(r['input_files']), int(r['output_files'])))
            raw.append(dict(kv_size=kv, system='baseline', kind='compaction', start_level=key[0], output_level=key[1],
                            input_bytes=r['input_bytes'], input_files=r['input_files'], output_files=r['output_files'],
                            elapsed_ms=round(int(r['total_tracked_us']) / 1e3, 3)))
        for key, v in sorted(groups.items()):
            levels.append(dict(kv_size=kv, system='baseline', kind='compaction', start_level=key[0], output_level=key[1],
                               jobs=len(v), total_sec=round(sum(x[0] for x in v) / 1e3, 3),
                               mean_ms=round(st.mean(x[0] for x in v), 3), median_ms=round(st.median(x[0] for x in v), 3),
                               p90_ms=round(q([x[0] for x in v], 0.9), 3),
                               mean_input_bytes=round(st.mean(x[1] for x in v)), mean_input_files=round(st.mean(x[2] for x in v), 2),
                               mean_output_files=round(st.mean(x[3] for x in v), 2),
                               note='intra-L0 (0->0) jobs have no virtual counterpart' if key == (0, 0) else ''))
        groups = {}
        with open(F2[kv]['run'] / 'events.tsv', newline='') as f:
            for r in csv.DictReader(f, delimiter='\t'):
                if r['stage'] != 'job_total' or r['kind'] not in ('virtual_compaction', 'virtual_trivial_move'):
                    continue
                key = (r['kind'], int(r['start_level']), int(r['output_level']))
                groups.setdefault(key, []).append((int(r['elapsed_ns']) / 1e6, int(r['input_bytes']),
                                                   int(r['input_files']), int(r['output_files'])))
                raw.append(dict(kv_size=kv, system='f2load', kind=r['kind'], start_level=key[1], output_level=key[2],
                                input_bytes=r['input_bytes'], input_files=r['input_files'], output_files=r['output_files'],
                                elapsed_ms=round(int(r['elapsed_ns']) / 1e6, 3)))
        for key, v in sorted(groups.items()):
            levels.append(dict(kv_size=kv, system='f2load', kind=key[0], start_level=key[1], output_level=key[2],
                               jobs=len(v), total_sec=round(sum(x[0] for x in v) / 1e3, 3),
                               mean_ms=round(st.mean(x[0] for x in v), 3), median_ms=round(st.median(x[0] for x in v), 3),
                               p90_ms=round(q([x[0] for x in v], 0.9), 3),
                               mean_input_bytes=round(st.mean(x[1] for x in v)), mean_input_files=round(st.mean(x[2] for x in v), 2),
                               mean_output_files=round(st.mean(x[3] for x in v), 2),
                               note='job time excludes the shared commit batch, reported once in the breakdown file'))
    write('paper_eval_compaction_vs_virtual.tsv',
          ['kv_size', 'system', 'kind', 'start_level', 'output_level', 'jobs', 'total_sec', 'mean_ms', 'median_ms', 'p90_ms',
           'mean_input_bytes', 'mean_input_files', 'mean_output_files', 'note'], levels)
    write('paper_eval_compaction_jobs_raw.tsv',
          ['kv_size', 'system', 'kind', 'start_level', 'output_level', 'input_bytes', 'input_files', 'output_files', 'elapsed_ms'], raw)


def main():
    breakdown()
    flush_vs_prepare()
    compaction()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
