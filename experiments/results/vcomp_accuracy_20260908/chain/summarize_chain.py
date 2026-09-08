#!/usr/bin/env python3
"""Summarize fixed-original-set descriptor closure measurements."""
import csv
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
rows = [json.loads(line) for line in (ROOT/'probe.jsonl').read_text().splitlines()]
assert len(rows) == 36
for row in rows:
    assert row['isolated_union_estimate'] == row['merged_target'] == row['output_descriptor_entries']
    assert row['raw_outputs_planned_entries'] == row['merged_target']
    assert row['output_descriptor_entries'] <= row['input_descriptor_entries']
    assert row['trimmed_outputs_diagnostic_only_library_unique_vs_stream_disagree_files'] == 0
    if row['kmv_samples'] == 16384:
        assert row['input_global_incomplete_sketches'] == row['output_global_incomplete_sketches'] == 0
        assert row['input_range_incomplete_sketches'] == row['output_range_incomplete_sketches'] == 0
(ROOT/'results.json').write_text(json.dumps(rows, indent=2) + '\n')
with (ROOT/'results.csv').open('w', newline='') as out:
    writer = csv.DictWriter(out, fieldnames=sorted(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
compact = ['case', 'kmv_samples', 'round', 'true_original_union', 'input_descriptor_entries',
           'merged_target', 'output_descriptor_entries', 'output_global_sample_entries',
           'output_global_sample_distinct_union', 'output_global_empty_sketches',
           'output_global_incomplete_sketches', 'output_range_sample_entries',
           'output_range_estimated_entry_sum', 'output_range_empty_sketches',
           'output_range_incomplete_sketches', 'output_original_keys_outside_all_descriptor_ranges',
           'raw_outputs_capacity_violations', 'trimmed_outputs_diagnostic_only_library_perfile_distinct_sum',
           'trimmed_outputs_diagnostic_only_library_distinct_union',
           'trimmed_outputs_diagnostic_only_library_missing_original_keys',
           'trimmed_outputs_diagnostic_only_library_invented_keys']
with (ROOT/'stage_summary.csv').open('w', newline='') as out:
    writer = csv.DictWriter(out, fieldnames=compact, extrasaction='ignore')
    writer.writeheader()
    writer.writerows(rows)

def series(selected, key):
    return ' → '.join(str(row[key]) for row in selected)

lines = ['# Repeated merge/split closure control', '',
    'This experiment starts from one generation of four exact input key sets (4,096 entries per file). Every later round merges all output virtual descriptors from the preceding round, then splits them again. No new keys are inserted. Raw virtual descriptors are carried forward; synthetic keys and materialization boundary trimming are diagnostic outputs only and never become the next round\'s input.', '',
    'The unchanged original exact key set is the oracle. Its distinct size is 16,384 for disjoint interleaving and 8,192 for hot/gapped overlap. A lossless representation should continue to represent that same union after remerging. Descriptor key-range support is compared with the original keys independently of sketch contents and independently of generated keys.', '',
    'All calls use the frozen production static library and native compiler flags. Samples are 512, 2,048, or 16,384, with eight buckets; the highest budget keeps every global and range sketch complete throughout these runs. Initial PLR error is eight, split target is 512 entries, no grandparent boundaries, and four rounds are measured.', '',
    '## Separate quantities', '',
    '- `true_original_union` is the immutable exact oracle U.',
    '- `merged_target` is the KMV-driven entry target D before splitting.',
    '- `output_descriptor_entries` is the sum of planned output entries; it equals D in every observation.',
    '- `library_distinct_union` counts distinct IDs from actual MaterializeKeys outputs after diagnostic same-level trimming. It is generated U, not D and not a measured SST-write count.',
    '- `global_sample_distinct_union` is the union of stored sample key IDs, not generated U. For complete sketches it tracks exact retained IDs, which can still differ from the original oracle after earlier range filtering.',
    '- `original_keys_outside_all_descriptor_ranges` measures original-key support lost by the current bounds, without using sample estimates.', '',
    '## Four-round trajectories', '',
    '| Dataset | Samples | Original U | Target D, rounds 1→4 | Generated U, rounds 1→4 | Retained global sample-ID union, rounds 1→4 |',
    '|---|---:|---:|---|---|---|']
for case in ('disjoint_interleaved', 'hot_shared_core', 'gapped_50'):
    for samples in (512, 2048, 16384):
        selected = sorted([row for row in rows if row['case'] == case and row['kmv_samples'] == samples], key=lambda row: row['round'])
        lines.append('| {} | {} | {} | {} | {} | {} |'.format(case, samples, selected[0]['true_original_union'],
            series(selected, 'merged_target'), series(selected, 'trimmed_outputs_diagnostic_only_library_distinct_union'),
            series(selected, 'output_global_sample_distinct_union')))
lines += ['', '## Complete-sketch control', '',
    '**Every input and output global/range sketch remains marked complete in every 16,384-sample round, yet retained sample-ID unions shrink.** This does not require random-sample exhaustion. The actual split bounds and the range-filtered descriptors can discard support for original keys. Subsequent union estimates can then exactly count the already-reduced retained set.', '',
    '| Complete-sketch dataset | Original keys outside current descriptor ranges, rounds 1→4 | Output capacity violations, rounds 1→4 | Global incomplete sketches, rounds 1→4 | Range incomplete sketches, rounds 1→4 |',
    '|---|---|---|---|---|']
for case in ('disjoint_interleaved', 'hot_shared_core', 'gapped_50'):
    selected = sorted([row for row in rows if row['case'] == case and row['kmv_samples'] == 16384], key=lambda row: row['round'])
    lines.append('| {} | {} | {} | {} | {} |'.format(case,
        series(selected, 'output_original_keys_outside_all_descriptor_ranges'),
        series(selected, 'raw_outputs_capacity_violations'), series(selected, 'output_global_incomplete_sketches'),
        series(selected, 'output_range_incomplete_sketches')))
lines += ['', 'For example, hot original U stays 8,192 while target D becomes 8,192 → 8,182 → 8,173 → 8,164 and the exact retained sample-ID union becomes 8,182 → 8,173 → 8,164 → 8,153. The original keys are never replaced by generated keys in this control.', '',
    'Current descriptor bounds can expand later without restoring previously filtered original sample IDs: the disjoint complete control has zero original keys outside current bounds in round three, but the retained complete sample-ID union is only 16,382. A complete flag therefore must not be mistaken for completeness relative to the initial oracle.', '',
    '## Shape drift without target shrink', '',
    'In the disjoint 512-sample case, target D stays exactly 16,384 in all four rounds, while actual library-generated U falls 16,381 → 15,656 → 15,475 → 15,451. Capacity-violating output files increase 2 → 17 → 17 → 19. Entry-count conservation alone therefore does not stop repeated shape/range distortion.', '',
    '## Limits and reproducibility', '',
    'This is an adversarial closure control, not the normal scheduler\'s exact compaction sequence. It establishes non-idempotent representation behavior with unchanged original keys; it does not quantify how much of the measured 100-GiB deficit comes from each path. The incomplete-sketch cases do not, by themselves, attribute every drop to sample depletion, count capping, or range filtering.', '',
    'No empty-global-sketch fallback is needed to obtain these failures. Full CSV/JSON retain global/range sample totals, empty/incomplete counts, range-estimated entry sums, per-file capacities, missing/invented original keys, raw helper vector sizes, and separate streaming-reference counts.', '',
    'MaterializeKeys raw vectors may contain repeated tail keys. Generated U here is an exact set count over actual library output; it is not labeled an SST write total. The native count-only streaming reference agrees with per-file deduplicated library output for all 36 observations.', '',
    'manifest.json records exact build/run commands, source hashes, binary hash, static-library hash, parameters, and successful exit status. Both included diagnostic sources are snapshotted here. The main library hash remained unchanged. Reproduce tables from saved observations with `python3 -B summarize_chain.py`; this writes only diagnostic summaries. No DB was opened or changed.']
(ROOT/'REPORT.md').write_text('\n'.join(lines) + '\n')
print('PASS: 36 closure records; target=split entry sum; native helper/reference agreement; all high-budget global/range sketches complete.')
