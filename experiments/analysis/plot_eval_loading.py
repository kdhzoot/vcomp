#!/usr/bin/env python3
"""Section 5.1 draft figures from the paper_eval_loading / flush / compaction TSVs."""
import csv
from collections import defaultdict
from pathlib import Path
import statistics as st

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np

EXP = Path(__file__).resolve().parents[1]
R = EXP / 'results'
FIGS = EXP.parents[1] / 'paper/figs'
BASE, F2 = '#1f5fa9', '#2a8a5c'
GRID, INK2 = '#cfcfcf', '#52514e'
KV = ['1KB', '91B']


def tsv(name):
    return list(csv.DictReader(open(R / name), delimiter='\t'))


def style(ax):
    for s in ('top', 'right'):
        ax.spines[s].set_visible(False)
    ax.grid(True, axis='x', color=GRID, linewidth=0.5, zorder=0)
    ax.set_axisbelow(True)


PROF = Path('/home/smrc/virtual_compaction/vcomp-prof-job/experiments/results')


def tsv_prof(kv):
    d = {'1KB': 'job_profile_1000gib_260913', '91B': 'job_profile_1000gib_91b_260913'}[kv]
    return list(csv.DictReader(open(PROF / d / 'job_times.tsv'), delimiter='\t'))


def fig_breakdown():
    rows = tsv('paper_eval_loading_breakdown.tsv')
    phases = ['setup', 'ingestion', 'drain_and_cleanup', 'materialization']
    labels = ['Setup', 'Ingestion (prepare + register)', 'Background drain', 'Materialization', 'Completion wait (fixed 5 s sleep)']
    colours = ['#9aa5b1', '#1f5fa9', '#d1662a', '#2a8a5c', '#ffffff']
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(13, 4.2), gridspec_kw=dict(width_ratios=[1, 1.25]))
    for i, kv in enumerate(KV):
        left = 0
        for ph, c, lab in zip(phases + ['waitforcompaction'], colours, labels):
            v = next(float(r['seconds']) for r in rows if r['kv_size'] == kv and r['stage'] == ph)
            ax.barh(i, v, left=left, color=c, edgecolor=INK2 if ph == 'waitforcompaction' else 'none',
                    hatch='///' if ph == 'waitforcompaction' else None, height=0.55, label=lab if i == 0 else None, zorder=3)
            if v > 8:
                ax.text(left + v / 2, i, '%.0f s' % v, ha='center', va='center', fontsize=8.5,
                        color='white' if ph != 'waitforcompaction' else INK2)
            left += v
        ax.text(left + 2, i, 'total %.0f s' % left, va='center', fontsize=9, color=INK2)
    ax.set_yticks(range(len(KV)))
    ax.set_yticklabels(['1 KB KV', '91 B KV'])
    ax.set_xlabel('wall-clock seconds (sequential phases)')
    ax.set_xlim(0, 330)
    ax.set_title('(a) F2Load loading time by phase, 1000 GiB', fontsize=10, loc='left')
    ax.legend(fontsize=8, frameon=False, loc='lower right', ncol=1)
    style(ax)

    jt = {kv: {(r['kind'], r['stage'], r['start_level'], r['output_level']): r
               for r in tsv_prof(kv)} for kv in KV}

    def cum(kv, kind, stage=None):
        return sum(float(r['inclusive_cumulative_sec']) for (k, st_, *_), r in jt[kv].items()
                   if k == kind and (stage is None or st_ == stage))

    # (row label, workers, [(stage label, value-fn)])
    rows_def = [
        ('vSST preparation', 8, [('sort + dedup + bitmap', lambda kv: cum(kv, 'prepare_stage', 'sort_dedup_membership')),
                                 ('KMV sketch', lambda kv: cum(kv, 'prepare_stage', 'kmv_descriptor_certify')),
                                 ('PLR model', lambda kv: cum(kv, 'prepare_stage', 'plr'))]),
        ('key generation\n(exact-membership stream)', 1, [('producer thread', lambda kv: cum(kv, 'key_input', 'stream_generate'))]),
        ('L0 registration', 1, [('LogAndApply', lambda kv: cum(kv, 'registration', 'log_apply')),
                                ('install / schedule / edit', lambda kv: cum(kv, 'registration', 'refill_batch') - cum(kv, 'registration', 'log_apply'))]),
        ('virtual compaction\n+ trivial moves', 48, [('merge', lambda kv: cum(kv, 'virtual_compaction', 'merge')),
                                                      ('split', lambda kv: cum(kv, 'virtual_compaction', 'split')),
                                                      ('DB mutex wait', lambda kv: cum(kv, 'virtual_compaction', 'mutex_wait')),
                                                      ('commit wait (batched)', lambda kv: cum(kv, 'virtual_compaction', 'commit_completion')),
                                                      ('other', lambda kv: cum(kv, 'virtual_compaction', 'job_total') + cum(kv, 'virtual_trivial_move', 'job_total')
                                                       - cum(kv, 'virtual_compaction', 'merge') - cum(kv, 'virtual_compaction', 'split')
                                                       - cum(kv, 'virtual_compaction', 'mutex_wait') - cum(kv, 'virtual_compaction', 'commit_completion')
                                                       - cum(kv, 'virtual_compaction', 'build_edit') - cum(kv, 'virtual_compaction', 'gather'))]),
        ('shared commit batches', 48, [('LogAndApply + registry', lambda kv: cum(kv, 'commit', 'log_apply_registry')),
                                       ('validate / other', lambda kv: cum(kv, 'commit', 'batch_total') - cum(kv, 'commit', 'log_apply_registry'))]),
        ('materialization', 48, [('SST write', lambda kv: cum(kv, 'materialize', 'sst')),
                                 ('audit + install (1 thread)', lambda kv: cum(kv, 'materialization_stage', 'audit_install'))]),
    ]
    palette = ['#1f5fa9', '#4f80c4', '#8fb1de', '#d1662a', '#c9d8ea']
    ys = np.arange(len(rows_def))
    for j, kv in enumerate(KV):
        for yi, (label, workers, stages) in enumerate(rows_def):
            left = 0
            y = yi + (0.2 if j else -0.2)
            for si, (sl, fn) in enumerate(stages):
                v = max(fn(kv), 0) / workers
                bx.barh(y, v, left=left, height=0.38, color=palette[si], zorder=3, edgecolor='white', linewidth=0.4,
                        alpha=1.0 if j == 0 else 0.55)
                if v > 6:
                    bx.text(left + v / 2, y, sl, ha='center', va='center', fontsize=6.5,
                            color='white' if si < 2 or si == 3 else INK2)
                left += v
            bx.text(left + 1.5, y, '%.1f s  %s' % (left, ['1 KB', '91 B'][j]), va='center', fontsize=7, color=INK2)
    bx.set_yticks(ys)
    bx.set_yticklabels([f'{lab}  (/{w})' for lab, w, _ in rows_def], fontsize=8)
    bx.invert_yaxis()
    bx.set_xlabel('cumulative seconds / worker count   (solid: 1 KB, faded: 91 B)')
    bx.set_title('(b) what each concurrent job kind spends its time on', fontsize=10, loc='left')
    bx.set_xlim(0, 240)
    style(bx)
    fig.tight_layout()
    fig.savefig(FIGS / 'eval_loading_breakdown_draft.png', dpi=170)


def fig_flush():
    rows = tsv('paper_eval_flush_vs_prepare.tsv')
    fig, axes = plt.subplots(1, 2, figsize=(9, 3.2))
    for ax, kv in zip(axes, KV):
        b = [r for r in rows if r['kv_size'] == kv and r['system'] == 'baseline' and r['category'] != 'total']
        f = [r for r in rows if r['kv_size'] == kv and r['system'] == 'f2load'
             and r['category'] not in ('total (prepare)', 'key generation (producer thread)')]
        bcol = ['#1f5fa9', '#4f80c4', '#8fb1de', '#c9d8ea']
        fcol = ['#2a8a5c', '#5aa982', '#93c7ab', '#c9e3d3']
        for x, group, cols in ((0, b, bcol), (1, f, fcol)):
            bottom = 0
            for r, c in zip(group, cols):
                v = float(r['mean_ms'])
                ax.bar(x, v, bottom=bottom, color=c, width=0.55, zorder=3, edgecolor='white', linewidth=0.5)
                if v > 0.06 * float([q for q in rows if q['kv_size'] == kv and q['system'] == 'baseline' and q['category'] == 'total'][0]['mean_ms']):
                    ax.text(x, bottom + v / 2, r['category'].replace('_', ' '), ha='center', va='center', fontsize=7.5,
                            color='white' if c in (bcol[0], bcol[1], fcol[0], fcol[1]) else INK2)
                bottom += v
            ax.text(x, bottom, '%.1f ms' % bottom, ha='center', va='bottom', fontsize=9)
        tb = float([q for q in rows if q['kv_size'] == kv and q['system'] == 'baseline' and q['category'] == 'total'][0]['mean_ms'])
        tf = sum(float(r['mean_ms']) for r in f)
        ax.annotate('%.1fx' % (tb / tf), xy=(1, tf), xytext=(1, tb * 0.55), ha='center', fontsize=11, color=F2,
                    arrowprops=dict(arrowstyle='-|>', color=F2, lw=1))
        ax.set_xticks([0, 1])
        ax.set_xticklabels(['baseline\nflush job', 'F2Load\nvSST batch'])
        ax.set_ylabel('mean time per job (ms)')
        ax.set_title({'1KB': '1 KB KV', '91B': '91 B KV'}[kv] + ', 64 MiB memtable', fontsize=10, loc='left')
        ax.set_ylim(0, tb * 1.18)
        # Zoomed copy of the F2Load bar so its composition is readable.
        ins = ax.inset_axes([0.60, 0.42, 0.36, 0.50])
        bottom = 0
        for r, c in zip(f, fcol):
            v = float(r['mean_ms'])
            ins.bar(0, v, bottom=bottom, color=c, width=0.6, edgecolor='white', linewidth=0.5)
            ins.text(0.38, bottom + v / 2, '%s  %.2f ms' % (r['category'].split(' (')[0].replace('_', ' '), v),
                     va='center', fontsize=6.5, color=INK2)
            bottom += v
        ins.set_xlim(-0.4, 1.6); ins.set_xticks([]); ins.set_ylim(0, bottom * 1.08)
        ins.set_title('F2Load batch, zoomed', fontsize=7.5, pad=2)
        ins.tick_params(labelsize=6.5)
        for sp in ('top', 'right'):
            ins.spines[sp].set_visible(False)
        for s in ('top', 'right'):
            ax.spines[s].set_visible(False)
        ax.grid(True, axis='y', color=GRID, linewidth=0.5, zorder=0)
        ax.set_axisbelow(True)
    fig.suptitle('Flush vs vSST preparation + registration, per 64 MiB batch', fontsize=10, x=0.02, ha='left')
    fig.tight_layout()
    fig.savefig(FIGS / 'eval_flush_vs_prepare_draft.png', dpi=170)


def fig_compaction():
    raw = tsv('paper_eval_compaction_jobs_raw.tsv')
    fig, axes2 = plt.subplots(2, 2, figsize=(10, 7.2), gridspec_kw=dict(height_ratios=[1.15, 1]))
    axes = axes2[0]
    lvl_col = {0: '#9aa5b1', 1: '#e0a04a', 2: '#d1662a', 3: '#8e4a9e', 4: '#1f5fa9', 5: '#2a8a5c'}
    for ax, kv in zip(axes, KV):
        for system, marker, alpha in (('baseline', 'o', 0.25), ('f2load', 's', 0.25)):
            pts = [r for r in raw if r['kv_size'] == kv and r['system'] == system and r['kind'] != 'virtual_trivial_move']
            x = np.array([int(r['input_bytes']) for r in pts]) / 2**30
            y = np.array([float(r['elapsed_ms']) for r in pts])
            c = [lvl_col[int(r['output_level'])] for r in pts]
            ax.scatter(x, y, s=4, c=c, marker=marker, alpha=alpha, linewidths=0, zorder=3, rasterized=True)
            # binned medians as the readable line
            bins = np.logspace(np.log10(max(x.min(), 0.01)), np.log10(x.max()), 14)
            idx = np.digitize(x, bins)
            bx_, by_ = [], []
            for b in range(1, len(bins)):
                sel = idx == b
                if sel.sum() >= 20:
                    bx_.append(np.sqrt(bins[b - 1] * bins[b])); by_.append(np.median(y[sel]))
            ax.plot(bx_, by_, color='black' if system == 'baseline' else 'black', lw=1.6,
                    ls='-' if system == 'baseline' else '--', zorder=5,
                    label='real compaction (binned median)' if system == 'baseline' else 'virtual compaction (binned median)')
        ax.set_xscale('log'); ax.set_yscale('log')
        ax.set_xlabel('compaction input (GiB)')
        ax.set_ylabel('job time (ms)')
        ax.set_title({'1KB': '1 KB KV', '91B': '91 B KV'}[kv], fontsize=10, loc='left')
        for s in ('top', 'right'):
            ax.spines[s].set_visible(False)
        ax.grid(True, which='major', color=GRID, linewidth=0.5, zorder=0)
        ax.set_axisbelow(True)
    for ax, kv in zip(axes2[1], KV):
        pts = [r for r in raw if r['kv_size'] == kv and r['system'] == 'f2load']
        x = np.array([int(r['input_bytes']) for r in pts]) / 2**30
        y = np.array([float(r['elapsed_ms']) for r in pts])
        c = [lvl_col[int(r['output_level'])] for r in pts]
        mv = np.array([r['kind'] == 'virtual_trivial_move' for r in pts])
        ax.scatter(x[~mv], y[~mv], s=4, c=[cc for cc, m in zip(c, mv) if not m], marker='s', alpha=0.3, linewidths=0, zorder=3, rasterized=True)
        ax.scatter(x[mv], y[mv], s=5, c='none', edgecolors=[cc for cc, m in zip(c, mv) if m], marker='o', alpha=0.5, linewidths=0.5, zorder=3, rasterized=True)
        for lvl in range(1, 6):
            sel = np.array([int(r['output_level']) == lvl and r['kind'] != 'virtual_trivial_move' for r in pts])
            if sel.sum() > 50:
                ax.axhline(np.median(y[sel]), color=lvl_col[lvl], lw=0.8, ls=':', zorder=2)
        ax.set_xscale('log')
        ax.set_ylim(0, 60)
        ax.set_xlabel('compaction input (GiB)')
        ax.set_ylabel('virtual job time (ms, linear)')
        ax.set_title({'1KB': '1 KB KV', '91B': '91 B KV'}[kv] + ' - virtual compaction only, zoomed (dotted: per-level median; hollow: trivial moves)', fontsize=9, loc='left')
        for sp in ('top', 'right'):
            ax.spines[sp].set_visible(False)
        ax.grid(True, color=GRID, linewidth=0.5, zorder=0)
        ax.set_axisbelow(True)
    handles = [plt.Line2D([], [], color='black', lw=1.6, label='real compaction, binned median'),
               plt.Line2D([], [], color='black', lw=1.6, ls='--', label='virtual compaction, binned median')]
    handles += [plt.Line2D([], [], marker='o', linestyle='', color=lvl_col[l], markersize=5, label=f'output L{l}') for l in range(1, 6)]
    fig.legend(handles=handles, loc='upper center', ncol=7, fontsize=8, frameon=False, bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(FIGS / 'eval_compaction_vs_virtual_draft.png', dpi=170)


if __name__ == '__main__':
    fig_breakdown(); fig_flush(); fig_compaction()
    print('drafts written to', FIGS)
