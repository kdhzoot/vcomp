#!/usr/bin/env python3
"""Export measured compaction job latency and exclusive stage breakdowns.

Four historical 1000 GiB captures (one run per engine/KV condition). This is
job-size scaling, not a dataset-size sweep or a repeated-load comparison.
"""
import argparse
import collections
import csv
import hashlib
import json
import math
import statistics as st
from pathlib import Path

EXP = Path(__file__).resolve().parents[1]
PROF = EXP.parent.parent / 'vcomp-prof-job' / 'experiments'
CONFIG = {
    '1KB': (EXP / 'artifacts/log_loads/figure2_1tb_fourcell_260903_run1/order1_kv1024_conventional/raw/compaction_breakdown.tsv',
            PROF / 'artifacts/job_profile/full_1000gib_260913_01/r1_on'),
    '91B': (EXP / 'artifacts/log_loads/figure2_1tb_fourcell_260903_resume1/order3_kv91_conventional/raw/compaction_breakdown.tsv',
            PROF / 'artifacts/job_profile/full_1000gib_91b_260913_01/r1_on'),
}
COMP = ['read', 'write', 'merge', 'compress', 'decompress', 'sst_build', 'other']
VCOMP = ['gather', 'merge', 'split', 'diagnostics', 'mutex_wait', 'build_edit',
         'commit_queue_wait', 'commit_after_queue', 'other']
ALL_STAGES = list(dict.fromkeys(COMP + VCOMP))


def read_tsv(path):
    with path.open(newline='') as f:
        yield from csv.DictReader(f, delimiter='\t')


def write_tsv(path, rows):
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter='\t', lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def quantile(values, p):
    values = sorted(values)
    pos = p * (len(values) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return values[lo] + (values[hi] - values[lo]) * (pos - lo)


def bin_bounds(value):
    if value == 0:
        return 0, 1
    low = 1 << (value.bit_length() - 1)
    return low, low * 2


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir', type=Path, required=True)
    ap.add_argument('--raw-output-dir', type=Path, required=True)
    args = ap.parse_args()
    out, raw_out = args.output_dir, args.raw_output_dir
    out.mkdir(parents=True, exist_ok=True)
    raw_out.mkdir(parents=True, exist_ok=True)
    jobs, sources, shared_commits, builds = [], [], [], {}

    def record(path):
        h = hashlib.sha256()
        with path.open('rb') as f:
            for block in iter(lambda: f.read(1024 * 1024), b''):
                h.update(block)
        sources.append(dict(path=str(path), sha256=h.hexdigest(), bytes=path.stat().st_size))

    def job(kv, engine, kind, raw, job_id, elapsed_ns, components, source, line):
        start, output = int(raw['start_level']), int(raw['output_level'])
        lower, upper = bin_bounds(int(raw['input_bytes']))
        row = dict(kv_size=kv, system=engine, kind=kind,
                   run_id=source.parent.parent.name,
                   job_id=str(job_id), start_level=start, output_level=output,
                   transition=f'L{start}->L{output}', input_bytes=int(raw['input_bytes']),
                   input_MiB=int(raw['input_bytes']) / 2**20, input_files=int(raw['input_files']),
                   output_bytes=int(raw['output_bytes']), output_files=int(raw['output_files']),
                   bin_lower_bytes=lower, bin_upper_exclusive_bytes=upper,
                   elapsed_ns=elapsed_ns, elapsed_ms=elapsed_ns / 1e6,
                   status=raw['status'],
                   primary_comparison=(kind in ('compaction', 'virtual_compaction') and output > start
                                       and raw['status'] in ('ok', '0')))
        for stage in ALL_STAGES:
            row[stage + '_ms'] = components[stage] / 1e6 if stage in components else None
        row['source_path'], row['source_line'] = str(source), line
        assert all(value >= 0 for value in components.values())
        assert sum(components.values()) == elapsed_ns
        jobs.append(row)

    for kv, (comp_path, vdir) in CONFIG.items():
        record(comp_path)
        record(comp_path.parent / 'load_cmd.sh')
        provenance = comp_path.parents[2] / 'provenance'
        for name in ('db_bench.sha256', 'vcomp_prof_commit.txt', 'vcomp_prof.diff', 'vcomp_compaction_profiler.h'):
            record(provenance / name)
        builds[kv] = dict(
            comp_binary_sha256=(provenance / 'db_bench.sha256').read_text().split()[0],
            comp_source_commit=(provenance / 'vcomp_prof_commit.txt').read_text().strip(),
            vcomp=json.loads((vdir.parent / 'provenance.json').read_text()))
        for line, r in enumerate(read_tsv(comp_path), 2):
            components = {stage: int(r[stage + '_us']) * 1000 for stage in COMP}
            job(kv, 'comp', 'compaction', r, r['job'], int(r['total_tracked_us']) * 1000,
                components, comp_path, line)

        events = vdir / 'events.tsv'
        record(events)
        for path in (vdir / 'command.json', vdir / 'validation.json', vdir.parent / 'provenance.json'):
            record(path)
        validated = json.loads((vdir / 'validation.json').read_text())
        assert validated['valid'] and validated['dropped'] == 0
        total, stages = {}, collections.defaultdict(dict)
        for line, r in enumerate(read_tsv(events), 2):
            if r['kind'] == 'commit' and r['stage'] == 'batch_total':
                shared_commits.append(dict(kv_size=kv, run_id=vdir.parent.name, batch_id=r['id'],
                    elapsed_ns=int(r['elapsed_ns']), elapsed_ms=int(r['elapsed_ns']) / 1e6,
                    input_files=int(r['input_files']), output_files=int(r['output_files']),
                    source_path=str(events), source_line=line))
            if r['kind'] not in ('virtual_compaction', 'virtual_trivial_move'):
                continue
            if r['stage'] == 'job_total':
                assert r['id'] not in total
                total[r['id']] = (r, line)
            else:
                assert r['stage'] not in stages[r['parent']]
                stages[r['parent']][r['stage']] = tuple(int(r[key]) for key in ('start_ns', 'end_ns', 'elapsed_ns'))
        for key, (r, line) in total.items():
            elapsed = int(r['elapsed_ns'])
            children = stages[key]
            components, intervals = {}, []
            for stage in ('gather', 'merge', 'split', 'diagnostics', 'mutex_wait', 'build_edit'):
                if stage in children:
                    start, end, duration = children[stage]
                    components[stage] = duration
                    intervals.append((start, end))
            start, end, completion = children['commit_completion']
            qs, qe, queue = children['commit_queue_wait']
            assert start == qs and start <= qe <= end
            components['commit_queue_wait'] = queue
            components['commit_after_queue'] = completion - queue
            intervals.append((start, end))
            intervals.sort()
            assert all(int(r['start_ns']) <= a <= b <= int(r['end_ns']) for a, b in intervals)
            assert all(a[1] <= b[0] for a, b in zip(intervals, intervals[1:]))
            components['other'] = elapsed - sum(components.values())
            job(kv, 'vcomp', r['kind'], r, key, elapsed, components, events, line)

    write_tsv(raw_out / 'jobs_with_breakdown.tsv', jobs)
    write_tsv(raw_out / 'shared_commit_batches.tsv', shared_commits)
    chosen = [r for r in jobs if r['primary_comparison']]
    # All means below weight jobs equally within one historical load.
    def summarize(group, extra):
        values = [r['elapsed_ms'] for r in group]
        result = dict(kv_size=group[0]['kv_size'], system=group[0]['system'], **extra,
                      n_load_runs=1, n_jobs=len(group),
                      mean_input_bytes=st.mean(r['input_bytes'] for r in group),
                      mean_input_MiB=st.mean(r['input_MiB'] for r in group),
                      mean_input_files=st.mean(r['input_files'] for r in group),
                      mean_output_files=st.mean(r['output_files'] for r in group),
                      mean_elapsed_ms=st.mean(values), median_elapsed_ms=st.median(values),
                      p90_elapsed_ms=quantile(values, .9), stddev_elapsed_ms=st.stdev(values) if len(values) > 1 else None,
                      cumulative_job_seconds=sum(values) / 1000)
        for stage in ALL_STAGES:
            measurements = [r[stage + '_ms'] for r in group if r[stage + '_ms'] is not None]
            result['mean_' + stage + '_ms'] = st.mean(measurements) if measurements else None
        assert abs(sum(result['mean_' + s + '_ms'] or 0 for s in ALL_STAGES) - result['mean_elapsed_ms']) < 1e-6
        return result

    grouped = collections.defaultdict(list)
    for r in chosen:
        grouped[r['kv_size'], r['system']].append(r)
    overall = [summarize(g, {}) for _, g in sorted(grouped.items())]
    write_tsv(out / 'mean_breakdown_wide.tsv', overall)
    breakdown = []
    for r in overall:
        for stage in COMP if r['system'] == 'comp' else VCOMP:
            ms = r['mean_' + stage + '_ms']
            breakdown.append(dict(kv_size=r['kv_size'], system=r['system'], stage=stage,
                                  mean_ms=ms, mean_total_ms=r['mean_elapsed_ms'],
                                  share_pct=100 * ms / r['mean_elapsed_ms'],
                                  n_jobs=r['n_jobs'], n_load_runs=1))
    write_tsv(out / 'mean_breakdown.tsv', breakdown)
    for levels in (False, True):
        grouped = collections.defaultdict(list)
        for r in chosen:
            key = (r['kv_size'], r['system'], r['bin_lower_bytes'], r['bin_upper_exclusive_bytes'])
            if levels:
                key += (r['start_level'], r['output_level'])
            grouped[key].append(r)
        aggregated = []
        for key, group in sorted(grouped.items()):
            extra = dict(bin_lower_bytes=key[2], bin_upper_exclusive_bytes=key[3],
                         bin_lower_MiB=key[2] / 2**20, bin_upper_MiB=key[3] / 2**20)
            if levels:
                extra.update(start_level=key[4], output_level=key[5])
            aggregated.append(summarize(group, extra))
        write_tsv(out / ('input_scaling_by_level.tsv' if levels else 'input_scaling.tsv'), aggregated)
        if not levels:
            scaling = aggregated

    inventory = []
    grouped = collections.defaultdict(list)
    for row in jobs:
        grouped[row['kv_size'], row['system'], row['kind'], row['start_level'], row['output_level'], row['status']].append(row)
    for key, group in sorted(grouped.items()):
        inventory.append(dict(kv_size=key[0], system=key[1], kind=key[2], start_level=key[3],
                              output_level=key[4], status=key[5], n_jobs=len(group),
                              used_in_primary_plot=group[0]['primary_comparison']))
    write_tsv(out / 'job_inventory.tsv', inventory)
    commit_summary = []
    for kv in CONFIG:
        group = [r for r in shared_commits if r['kv_size'] == kv]
        commit_summary.append(dict(kv_size=kv, n_batches=len(group), mean_batch_ms=st.mean(r['elapsed_ms'] for r in group),
                                   cumulative_batch_seconds=sum(r['elapsed_ms'] for r in group) / 1000,
                                   note='Shared work counted once per batch; do not add to per-job latency breakdown.'))
    write_tsv(out / 'shared_commit_summary.tsv', commit_summary)
    manifest = dict(analysis='Historical RocksDB compaction vs F2Load virtual job latency',
        measured_builds=builds,
        dataset_GiB=1000, repetitions_per_condition=1,
        time_units='Source compaction integer microseconds; virtual integer nanoseconds. Exports milliseconds.',
        x_axis='Sum of input SST metadata sizes: real physical SST sizes vs virtual descriptor estimated SST sizes. Not actual VComp read I/O.',
        mean='Unweighted arithmetic mean over jobs, not mean across loading repetitions.',
        primary_selection='Successful inter-level rewrite jobs only. Intra-L0 and virtual trivial moves remain in raw/inventory.',
        bins='Powers of two in bytes, left inclusive and right exclusive; no invented empty-bin values.',
        plot_filter='Bins with fewer than 10 jobs retained in TSV but omitted from plotted mean curves. Figure zooms to input >=4 MiB; all bins remain in TSV.',
        comparability='Different dates/builds. Baseline max_write_buffer_number=2; F2Load=16. Baseline tracked time excludes pre-Install DB-mutex reacquisition; virtual job includes mutex waiting. Not identical jobs or a controlled matched-run speedup.',
        timing='VComp job_total includes commit_completion; queue_wait is nested within completion. commit_after_queue=completion-queue_wait. other=job_total-sum(exclusive stages). Shared batch time is never added to job latency.',
        raw_jobs=str(raw_out / 'jobs_with_breakdown.tsv'), raw_shared_commits=str(raw_out / 'shared_commit_batches.tsv'),
        raw_job_count=len(jobs), primary_job_count=len(chosen),
        analysis_script=str(Path(__file__).resolve()),
        analysis_script_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), sources=sources,
        validation=dict(stage_sums_equal_job_totals=True, virtual_stage_intervals_disjoint=True,
                        virtual_queue_wait_is_nested_in_completion=True, capture_dropped_records=0))
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    plot(out, scaling, breakdown)
    print(json.dumps(dict(raw_jobs=len(jobs), plotted_job_population=len(chosen), output=str(out), overall=overall)))


def plot(out, scaling, breakdown):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import FuncFormatter
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5), layout='constrained')
    for kv, ax in zip(CONFIG, axes):
        for system, color, label in [('comp', '#2a78d6', 'Compaction'), ('vcomp', '#e57824', 'Virtual compaction')]:
            rows = [r for r in scaling if r['kv_size'] == kv and r['system'] == system
                    and r['n_jobs'] >= 10 and r['bin_lower_MiB'] >= 4]
            ax.plot([r['mean_input_MiB'] for r in rows], [r['mean_elapsed_ms'] for r in rows], 'o-', color=color, label=label)
        ax.set_xscale('log', base=2)
        ax.set_yscale('log')
        ax.set_xticks([4, 16, 64, 256, 1024, 4096])
        ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f'{value:,.0f}'))
        ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f'{value/1000:g}K' if value >= 1000 else f'{value:g}'))
        ax.set_xlabel('Mean input SST metadata size (MiB)')
        ax.set_ylabel('Mean tracked job latency (ms)')
        ax.set_title(f'{kv} KV · inter-level rewrite jobs')
        ax.grid(which='major', alpha=.2)
        ax.legend()
    fig.suptitle('Historical 1000 GiB runs · one load per condition · input ≥4 MiB, ≥10 jobs/bin', fontsize=11)
    fig.savefig(out / 'input_scaling.png', dpi=160)
    fig.savefig(out / 'input_scaling.pdf')
    plt.close(fig)
    fig, axes = plt.subplots(2, 2, figsize=(11, 7), layout='constrained')
    palette = plt.get_cmap('tab20')
    for i, kv in enumerate(CONFIG):
        for j, system in enumerate(('comp', 'vcomp')):
            ax = axes[i, j]
            rows = [r for r in breakdown if r['kv_size'] == kv and r['system'] == system]
            bottom = 0
            for k, r in enumerate(rows):
                if not r['mean_ms']:
                    continue
                ax.bar([0], [r['mean_ms']], bottom=bottom, color=palette(k), label=r['stage'], width=.45)
                bottom += r['mean_ms']
            ax.set_xticks([0], [f"{system} · n={rows[0]['n_jobs']:,}"])
            ax.set_ylabel('Mean job latency (ms)')
            ax.set_title(f'{kv} · {bottom:.3f} ms/job')
            ax.legend(fontsize=7, loc='upper left', bbox_to_anchor=(1, 1))
    fig.suptitle('Exclusive per-job breakdown · each panel has its own y-scale', fontsize=11)
    fig.savefig(out / 'mean_breakdown.png', dpi=160)
    fig.savefig(out / 'mean_breakdown.pdf')
    plt.close(fig)


if __name__ == '__main__':
    main()
