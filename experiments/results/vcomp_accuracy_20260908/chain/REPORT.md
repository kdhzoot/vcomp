# Repeated merge/split closure control

This experiment starts from one generation of four exact input key sets (4,096 entries per file). Every later round merges all output virtual descriptors from the preceding round, then splits them again. No new keys are inserted. Raw virtual descriptors are carried forward; synthetic keys and materialization boundary trimming are diagnostic outputs only and never become the next round's input.

The unchanged original exact key set is the oracle. Its distinct size is 16,384 for disjoint interleaving and 8,192 for hot/gapped overlap. A lossless representation should continue to represent that same union after remerging. Descriptor key-range support is compared with the original keys independently of sketch contents and independently of generated keys.

All calls use the frozen production static library and native compiler flags. Samples are 512, 2,048, or 16,384, with eight buckets; the highest budget keeps every global and range sketch complete throughout these runs. Initial PLR error is eight, split target is 512 entries, no grandparent boundaries, and four rounds are measured.

## Separate quantities

- `true_original_union` is the immutable exact oracle U.
- `merged_target` is the KMV-driven entry target D before splitting.
- `output_descriptor_entries` is the sum of planned output entries; it equals D in every observation.
- `library_distinct_union` counts distinct IDs from actual MaterializeKeys outputs after diagnostic same-level trimming. It is generated U, not D and not a measured SST-write count.
- `global_sample_distinct_union` is the union of stored sample key IDs, not generated U. For complete sketches it tracks exact retained IDs, which can still differ from the original oracle after earlier range filtering.
- `original_keys_outside_all_descriptor_ranges` measures original-key support lost by the current bounds, without using sample estimates.

## Four-round trajectories

| Dataset | Samples | Original U | Target D, rounds 1→4 | Generated U, rounds 1→4 | Retained global sample-ID union, rounds 1→4 |
|---|---:|---:|---|---|---|
| disjoint_interleaved | 512 | 16384 | 16384 → 16384 → 16384 → 16384 | 16381 → 15656 → 15475 → 15451 | 1800 → 1780 → 1773 → 1773 |
| disjoint_interleaved | 2048 | 16384 | 16384 → 16359 → 16359 → 16359 | 16379 → 16068 → 16015 → 16000 | 7808 → 7778 → 7778 → 7764 |
| disjoint_interleaved | 16384 | 16384 | 16384 → 16383 → 16382 → 16382 | 16382 → 16382 → 16372 → 16349 | 16383 → 16382 → 16382 → 16381 |
| hot_shared_core | 512 | 8192 | 8537 → 8537 → 8537 → 8537 | 8307 → 8401 → 8349 → 8356 | 938 → 925 → 920 → 911 |
| hot_shared_core | 2048 | 8192 | 8315 → 8287 → 8276 → 8267 | 8277 → 8240 → 8217 → 8204 | 3911 → 3885 → 3882 → 3875 |
| hot_shared_core | 16384 | 8192 | 8192 → 8182 → 8173 → 8164 | 8192 → 8181 → 8170 → 8158 | 8182 → 8173 → 8164 → 8153 |
| gapped_50 | 512 | 8192 | 8184 → 7986 → 7963 → 7941 | 8180 → 7986 → 7963 → 7941 | 913 → 899 → 896 → 884 |
| gapped_50 | 2048 | 8192 | 8148 → 8148 → 8138 → 8124 | 8148 → 8148 → 8138 → 8124 | 3914 → 3928 → 3916 → 3895 |
| gapped_50 | 16384 | 8192 | 8192 → 8191 → 8191 → 8184 | 8192 → 8191 → 8191 → 8184 | 8191 → 8191 → 8184 → 8178 |

## Complete-sketch control

**Every input and output global/range sketch remains marked complete in every 16,384-sample round, yet retained sample-ID unions shrink.** This does not require random-sample exhaustion. The actual split bounds and the range-filtered descriptors can discard support for original keys. Subsequent union estimates can then exactly count the already-reduced retained set.

| Complete-sketch dataset | Original keys outside current descriptor ranges, rounds 1→4 | Output capacity violations, rounds 1→4 | Global incomplete sketches, rounds 1→4 | Range incomplete sketches, rounds 1→4 |
|---|---|---|---|---|
| disjoint_interleaved | 1 → 2 → 0 → 1 | 1 → 1 → 6 → 3 | 0 → 0 → 0 → 0 | 0 → 0 → 0 → 0 |
| hot_shared_core | 10 → 10 → 9 → 12 | 0 → 1 → 2 → 2 | 0 → 0 → 0 → 0 | 0 → 0 → 0 → 0 |
| gapped_50 | 1 → 0 → 7 → 7 | 0 → 0 → 0 → 0 | 0 → 0 → 0 → 0 | 0 → 0 → 0 → 0 |

For example, hot original U stays 8,192 while target D becomes 8,192 → 8,182 → 8,173 → 8,164 and the exact retained sample-ID union becomes 8,182 → 8,173 → 8,164 → 8,153. The original keys are never replaced by generated keys in this control.

Current descriptor bounds can expand later without restoring previously filtered original sample IDs: the disjoint complete control has zero original keys outside current bounds in round three, but the retained complete sample-ID union is only 16,382. A complete flag therefore must not be mistaken for completeness relative to the initial oracle.

## Shape drift without target shrink

In the disjoint 512-sample case, target D stays exactly 16,384 in all four rounds, while actual library-generated U falls 16,381 → 15,656 → 15,475 → 15,451. Capacity-violating output files increase 2 → 17 → 17 → 19. Entry-count conservation alone therefore does not stop repeated shape/range distortion.

## Limits and reproducibility

This is an adversarial closure control, not the normal scheduler's exact compaction sequence. It establishes non-idempotent representation behavior with unchanged original keys; it does not quantify how much of the measured 100-GiB deficit comes from each path. The incomplete-sketch cases do not, by themselves, attribute every drop to sample depletion, count capping, or range filtering.

No empty-global-sketch fallback is needed to obtain these failures. Full CSV/JSON retain global/range sample totals, empty/incomplete counts, range-estimated entry sums, per-file capacities, missing/invented original keys, raw helper vector sizes, and separate streaming-reference counts.

MaterializeKeys raw vectors may contain repeated tail keys. Generated U here is an exact set count over actual library output; it is not labeled an SST write total. The native count-only streaming reference agrees with per-file deduplicated library output for all 36 observations.

manifest.json records exact build/run commands, source hashes, binary hash, static-library hash, parameters, and successful exit status. Both included diagnostic sources are snapshotted here. The main library hash remained unchanged. Reproduce tables from saved observations with `python3 -B summarize_chain.py`; this writes only diagnostic summaries. No DB was opened or changed.
