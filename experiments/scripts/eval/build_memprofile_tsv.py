#!/usr/bin/env python3
"""TSVs for the f2load_memprofile_260914 campaign: F2Load loading memory.

Six F2Load loadings (1/2/4 TB x 1 KB/100 B KV, build a04221e7) with RSS and its
components sampled every 100 ms. Four tables:

  *.tsv              one row per cell, peak RSS and its named components
  *_components.tsv   the peak-RSS sample split into six terms that sum to RSS
  *_phases.tsv       each load phase's own RSS peak and what was resident then
  *_scaling.tsv      per-doubling growth, the fitted model, and its projections

This campaign describes F2Load on its own; it carries no baseline loader and no
other build.
"""
import csv
import json
from pathlib import Path
import statistics as st

EXP = Path(__file__).resolve().parents[2]
# The campaign was extended with 3 TB and 5 TB in a second run; both carry the
# same binary and protocol, so the tables merge them and sort by scale.
SRCS = sorted((EXP / 'artifacts/log_loads').glob('f2load_memprofile_*/results.json'))
OUT = EXP / 'results'

DRAM_BYTES = 1.0 * (1024 ** 4)   # the machine's 1 TiB of DRAM

CELL_FIELDS = [
    'arm', 'dataset_gib', 'kv_bytes', 'key_bytes', 'value_bytes', 'records',
    'elapsed_sec', 'load_gib_per_sec', 'status', 'peak_phase', 'peak_files',
    'baseline_rss', 'peak_rss_hwm', 'overhead_hwm',
    'peak_plr_segment_bytes', 'peak_descriptor_total', 'peak_registry_index_bytes',
    'peak_descriptor_object_bytes', 'peak_other_live', 'peak_kmv_bucket_bytes',
    'peak_kmv_sample_bytes', 'peak_block_cache', 'peak_memtables',
    'peak_je_allocated', 'peak_je_resident', 'peak_je_retained',
    'je_fragmentation_bytes', 'peak_rss_minus_allocated',
    'overhead_bytes_per_record', 'plr_bytes_per_record', 'overhead_bytes_per_sst',
    'plr_share_of_overhead', 'overhead_bytes_per_dataset_gib',
    'peak_rss_pct_of_dram', 'l5_files', 'l6_files', 'samples', 'sampling_miss',
]

PHASE_FIELDS = [
    'arm', 'dataset_gib', 'kv_bytes', 'phase', 'samples', 'span_sec',
    'max_rss_bytes', 'at_max_rss_plr_bytes', 'at_max_rss_other_live_bytes',
    'at_max_rss_files', 'max_other_live_bytes', 'max_other_live_t_sec',
    'is_global_rss_peak', 'registry_torn_down',
]

COMPONENT_FIELDS = [
    'arm', 'dataset_gib', 'kv_bytes', 'records', 'peak_sample_t_sec', 'peak_phase',
    'peak_rss_bytes', 'component', 'bytes', 'share_of_rss', 'bytes_per_record',
]

# The profiler's own identities, verified on every cell before the split is used:
#   descriptor_total = plr + kmv_sample + kmv_bucket + registry_index + descriptor_object
#   je_allocated     = descriptor_total + other_live + block_cache + memtables
# What is left of RSS after je_allocated is allocator overhead plus everything
# off the heap (binary, stacks, page tables). jemalloc's own `resident` runs
# above process RSS here, so it is not used as a term.
COMPONENTS = [
    ('PLR segments', lambda s: s['plr_segment_bytes']),
    ('KMV sketches', lambda s: s['kmv_sample_bytes'] + s['kmv_bucket_bytes']),
    ('registry index + descriptor objects',
     lambda s: s['registry_index_bytes'] + s['descriptor_object_bytes']),
    ('other live heap', lambda s: s['other_live'] + s['block_cache'] + s['memtables']),
    ('allocator + non-heap', lambda s: s['rss'] - s['je_allocated']),
    ('idle RSS', lambda s: s['base_rss']),
]

SCALE_FIELDS = ['build', 'kv_bytes', 'dataset_gib', 'kind', 'records', 'sst_files',
                'peak_rss_bytes', 'peak_rss_gb', 'bytes_per_record',
                'growth_vs_previous_doubling', 'model_bytes', 'model_error_pct']


def profile_path(arm):
    for src in SRCS:
        candidate = src.parent / arm / 'profile.jsonl'
        if candidate.exists():
            return candidate
    raise FileNotFoundError('no profile for ' + arm)


def cells():
    rows = []
    for src in SRCS:
        if src.exists():
            rows.extend(json.loads(src.read_text()))
    out = []
    for r in rows:
        levels = {k: v['files'] for k, v in r['levels'].items()}
        overhead, records, files = r['overhead_hwm'], r['records'], r['peak_files']
        out.append(dict(
            r, load_gib_per_sec=round(r['dataset_gib'] / r['elapsed_sec'], 3),
            je_fragmentation_bytes=r['peak_je_resident'] - r['peak_je_allocated'],
            overhead_bytes_per_record=round(overhead / records, 5),
            plr_bytes_per_record=round(r['peak_plr_segment_bytes'] / records, 5),
            overhead_bytes_per_sst=round(overhead / files, 1),
            plr_share_of_overhead=round(r['peak_plr_segment_bytes'] / overhead, 5),
            overhead_bytes_per_dataset_gib=round(overhead / r['dataset_gib'], 1),
            peak_rss_pct_of_dram=round(100 * r['peak_rss_hwm'] / DRAM_BYTES, 4),
            l5_files=levels.get('5', 0), l6_files=levels.get('6', 0)))
    return sorted(out, key=lambda r: (-r['kv_bytes'], r['dataset_gib']))


def fit(rows):
    """overhead = c1 * records + c2 * files, no intercept."""
    x = [(r['records'], r['peak_files']) for r in rows]
    y = [r['overhead_hwm'] for r in rows]
    a11 = sum(p[0] ** 2 for p in x); a12 = sum(p[0] * p[1] for p in x)
    a22 = sum(p[1] ** 2 for p in x)
    b1 = sum(p[0] * v for p, v in zip(x, y)); b2 = sum(p[1] * v for p, v in zip(x, y))
    det = a11 * a22 - a12 * a12
    c1 = (b1 * a22 - b2 * a12) / det
    c2 = (a11 * b2 - a12 * b1) / det
    mean = st.mean(y)
    r2 = 1 - (sum((v - (c1 * p[0] + c2 * p[1])) ** 2 for p, v in zip(x, y))
              / sum((v - mean) ** 2 for v in y))
    return c1, c2, r2


def scaling(rows, c1, c2):
    out = []
    files_per_gib = {r['kv_bytes']: r['peak_files'] / r['dataset_gib'] for r in rows}
    for kv in (1024, 100):
        series = [r for r in rows if r['kv_bytes'] == kv]
        previous = None
        for r in series:
            model = c1 * r['records'] + c2 * r['peak_files']
            out.append(dict(
                build='a04221e7 (2026-09-14)', kv_bytes=kv, dataset_gib=r['dataset_gib'],
                kind='measured', records=r['records'], sst_files=r['peak_files'],
                peak_rss_bytes=r['peak_rss_hwm'], peak_rss_gb=round(r['peak_rss_hwm'] / 1e9, 3),
                bytes_per_record=round(r['overhead_hwm'] / r['records'], 5),
                growth_vs_previous_doubling=(round(r['peak_rss_hwm'] / previous, 3)
                                             if previous else ''),
                model_bytes=round(model), model_error_pct=round(
                    100 * (model - r['overhead_hwm']) / r['overhead_hwm'], 2)))
            previous = r['peak_rss_hwm']
    for kv in (1024, 100):
        for gib in (8000, 16000, 32000):
            records = gib * (1024 ** 3) / kv
            files = files_per_gib[kv] * gib
            model = c1 * records + c2 * files
            out.append(dict(
                build='a04221e7 (2026-09-14)', kv_bytes=kv, dataset_gib=gib,
                kind='projected', records=int(records), sst_files=int(files),
                peak_rss_bytes='', peak_rss_gb='', bytes_per_record=round(c1, 5),
                growth_vs_previous_doubling='', model_bytes=round(model),
                model_error_pct=''))
    return out


def peak_sample(arm):
    """The single 100 ms sample at which RSS was highest."""
    path = profile_path(arm)
    best = None
    for line in path.open():
        sample = json.loads(line)
        if best is None or sample['rss'] > best['rss']:
            best = sample
    parts = best['plr_segment_bytes'] + best['kmv_sample_bytes'] + best['kmv_bucket_bytes'] \
        + best['registry_index_bytes'] + best['descriptor_object_bytes']
    assert parts == best['descriptor_total'], arm + ': descriptor_total identity broken'
    assert best['descriptor_total'] + best['other_live'] + best['block_cache'] \
        + best['memtables'] == best['je_allocated'], arm + ': allocated identity broken'
    return best


def components(rows):
    out = []
    for r in rows:
        sample = peak_sample(r['arm'])
        rss = sample['rss']
        # base_rss is inside rss already; the five live terms plus it sum to rss.
        for name, get in COMPONENTS:
            value = get(sample) if name != 'allocator + non-heap' \
                else rss - sample['je_allocated'] - sample['base_rss']
            out.append(dict(
                arm=r['arm'], dataset_gib=r['dataset_gib'], kv_bytes=r['kv_bytes'],
                records=r['records'], peak_sample_t_sec=round(sample['t_ms'] / 1000, 1),
                peak_phase=sample['phase'], peak_rss_bytes=rss, component=name,
                bytes=value, share_of_rss=round(value / rss, 6),
                bytes_per_record=round(value / r['records'], 6)))
        total = sum(e['bytes'] for e in out[-len(COMPONENTS):])
        assert abs(total - rss) < 2, '{}: components sum to {} not {}'.format(
            r['arm'], total, rss)
    return out


def phases(rows):
    """Each phase's own RSS peak. The load's peak is not the only one that
    matters: the ingestion buffers in other_live peak much earlier, while the
    PLR model is still small, so a cell has two humps that do not coincide."""
    out = []
    for r in rows:
        samples = [json.loads(line) for line in profile_path(r['arm']).open()]
        global_peak = max(s['rss'] for s in samples)
        groups = {}
        for s in samples:
            groups.setdefault(s['phase'], []).append(s)
        for phase, group in groups.items():
            top = max(group, key=lambda s: s['rss'])
            fattest = max(group, key=lambda s: s['other_live'])
            out.append(dict(
                arm=r['arm'], dataset_gib=r['dataset_gib'], kv_bytes=r['kv_bytes'],
                phase=phase, samples=len(group),
                span_sec=round((group[-1]['t_ms'] - group[0]['t_ms']) / 1000, 1),
                max_rss_bytes=top['rss'], at_max_rss_plr_bytes=top['plr_segment_bytes'],
                at_max_rss_other_live_bytes=top['other_live'],
                at_max_rss_files=top['files'],
                max_other_live_bytes=fattest['other_live'],
                max_other_live_t_sec=round(fattest['t_ms'] / 1000, 1),
                is_global_rss_peak=int(top['rss'] == global_peak),
                # Past ingestion, files==0 means the registry has been torn
                # down while its memory is still allocated, so that phase's
                # other_live is an accounting artefact, not a real allocation.
                registry_torn_down=int(fattest['files'] == 0 and phase != 'init')))
    return out


def write(path, fieldnames, data):
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, delimiter='\t',
                                extrasaction='ignore')
        writer.writeheader()
        writer.writerows(data)
    print('wrote {} ({} rows)'.format(path, len(data)))


def main():
    OUT.mkdir(exist_ok=True)
    rows = cells()
    c1, c2, r2 = fit(rows)
    print('fit: overhead = {:.4f} B/record * records + {:.1f} KB/SST * files   R^2 = {:.5f}'
          .format(c1, c2 / 1024, r2))
    write(OUT / 'f2load_memprofile_260914.tsv', CELL_FIELDS, rows)
    write(OUT / 'f2load_memprofile_260914_scaling.tsv', SCALE_FIELDS, scaling(rows, c1, c2))
    write(OUT / 'f2load_memprofile_260914_components.tsv', COMPONENT_FIELDS, components(rows))
    write(OUT / 'f2load_memprofile_260914_phases.tsv', PHASE_FIELDS, phases(rows))
    (OUT / 'f2load_memprofile_260914_model.json').write_text(json.dumps(dict(
        model='overhead_bytes = c_record * records + c_sst * sst_files',
        c_record_bytes=c1, c_sst_bytes=c2, r_squared=r2, cells=len(rows),
        sources=[str(s) for s in SRCS if s.exists()]), indent=2) + '\n')
    print('wrote {}'.format(OUT / 'f2load_memprofile_260914_model.json'))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
