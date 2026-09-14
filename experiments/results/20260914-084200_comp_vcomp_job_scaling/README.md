# Compaction / virtual-compaction job data

RocksDB/F2Load의 **개별 작업 입력 크기** 기준 자료다. 전체 데이터셋 크기를 바꾼 sweep이 아니다. 1000 GiB 로딩에서 KV 1KB(24+1000 B)와 91B(48+43 B)를 각각 측정했으며, 엔진/KV 조건별 로딩은 1회다. 아래 평균은 해당 로딩에서 발생한 작업들의 산술평균이다. 이번 정리는 기존 로그 분석이며 신규 로딩·벤치마크를 실행하지 않았다.

| 용도 | 파일 |
|---|---|
| 평균 시간 stacked breakdown | [mean_breakdown.tsv](mean_breakdown.tsv): kv_size, system, stage, mean_ms, share_pct, n_jobs |
| 입력 크기 증가에 따른 평균 시간 | [input_scaling.tsv](input_scaling.tsv): mean_input_MiB → mean_elapsed_ms; 각 bin의 n_jobs, median/p90/stddev와 단계별 평균 포함 |
| 레벨을 고정한 입력 크기별 비교 | [input_scaling_by_level.tsv](input_scaling_by_level.tsv): start_level/output_level 추가 |
| 작업별 전체 가공 raw, 212,932행 | [jobs_with_breakdown.tsv](../../artifacts/analysis/20260914-084200_comp_vcomp_job_scaling/jobs_with_breakdown.tsv), 77 MiB; job_id/input_bytes/elapsed_ns 및 모든 분해 항목 |
| 평균 breakdown 그림 | [PNG](mean_breakdown.png), [PDF](mean_breakdown.pdf) |
| 입력 크기별 평균 그림 | [PNG](input_scaling.png), [PDF](input_scaling.pdf) |
| 전체 작업 종류/레벨/선택 여부 | [job_inventory.tsv](job_inventory.tsv) |
| 공유 commit batch 비용, 별도 집계 | [shared_commit_summary.tsv](shared_commit_summary.tsv) |

주 비교와 그림에는 성공한 inter-level rewrite 작업만 사용했다. Baseline의 intra-L0(0→0), VComp의 trivial move는 전체 raw에 보존하되 주 비교에서 분리했다. 주 비교 작업 수는 195,184개다.

| KV | System | 평균 시간 (ms/job) | 작업 수 |
|---|---|---:|---:|
| 1KB | comp | 710.305 | 44,459 |
| 1KB | vcomp | 10.103 | 48,169 |
| 91B | comp | 1744.455 | 50,630 |
| 91B | vcomp | 9.833 | 51,926 |

입력 크기 bin은 바이트 기준 2의 거듭제곱 구간 [lower, upper)이다. 예: 64–128 MiB, 128–256 MiB. 그래프의 x는 각 bin 안에서 측정된 실제 평균 input size, y는 평균 job time이다. 빈 구간을 만들어 채우지 않았다. TSV에는 모든 구간을 보존하며, 미리보기 그림은 입력 4 MiB 이상·10개 이상 작업이 있는 bin만 표시한다. 91B comp에 존재하는 수 KiB 입력 작업도 raw/TSV에는 남아 있다. 파일 수에 따른 추가 필터링은 raw의 input_files로 할 수 있다.

1KB의 64–128 MiB → 512–1024 MiB 구간에서 comp는 343.177 → 1583.011 ms, vcomp는 9.068 → 10.784 ms다. 91B의 같은 구간에서는 comp 715.749 → 3737.237 ms, vcomp 9.111 → 10.991 ms다. 이는 서로 다른 작업 집단의 관측 평균이며 통제된 동일-job speedup은 아니다.

시간 분해는 다음과 같다.

- Comp: read + write + merge + compress + decompress + sst_build + other = total_tracked_us. merge는 실제 key merge, SST build는 I/O 등 중첩 구간을 제외한 계측 항목이다. 원본의 process_kv_us/run_subcompactions_us 또는 install_total_us/log_and_apply_us를 다시 더하면 중복된다.
- VComp: gather + merge + split + diagnostics + mutex_wait + build_edit + commit_queue_wait + commit_after_queue + other = job_total. 여기서 merge/split은 모델/descriptor 작업이다.
- commit_completion에는 commit_queue_wait가 포함된다. commit_after_queue는 completion에서 queue wait를 뺀 시간으로, 순수 commit CPU 시간이 아니다.
- other는 job_total에서 위의 겹치지 않는 항목을 뺀 잔여 시간이다. commit leader가 다른 batch를 처리하는 시간, follower의 깨어남/반환 지연, 로그·객체 정리 등이 남을 수 있으므로 계산 시간으로 단정하지 않는다.
- shared commit/batch_total은 여러 job과 겹친다. job latency breakdown에 더하지 않고 별도 표로 남겼다.
- 모든 원본 comp의 7개 항목 합과 total이 일치함을 확인했다. 모든 vcomp의 단계 구간이 job_total 내부에 있고 서로 겹치지 않으며 잔여가 음수가 아님도 확인했다.

기존 build_eval_loading_tsv.py의 “job time excludes the shared commit batch” 주석은 별도 batch 행을 제외했다는 뜻으로만 맞다. 실제 job_total에는 commit 대기/완료 latency가 이미 포함되어 있다. 이 새 bundle은 그 의미를 바로잡았으며 기존 수치나 파일을 덮어쓰지 않았다.

비교 조건의 한계: baseline은 2026-09-03 vcomp-prof, virtual은 2026-09-13 vcomp-prof-job이며 baseline max_write_buffer_number=2, F2Load=16이다. 공통으로 max_background_jobs=48, subcompactions=1, 64 MiB memtable/SST, level base 256 MiB, multiplier 10, format 6, compression/WAL off다. Baseline tracked total은 Install 직전 DB mutex 재획득 대기를 포함하지 않지만 VComp job_total은 mutex 대기를 포함한다. 같은 경계의 CPU 성능비나 반복 실행 평균으로 해석하면 안 된다.

input_bytes는 두 엔진 모두 picker 입력 파일들의 metadata size 합이다. **Comp는 실제 SST 크기이고, VComp는 모델이 추정해 등록한 SST 크기다. VComp가 그만큼 읽었다는 뜻이 아니다.** 레벨, 파일 수, 입력 규모가 다르므로 원본 작업을 일대일로 짝지었다고 가정하지 않았다.

원본 경로:

- Comp 1KB: `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_loads/figure2_1tb_fourcell_260903_run1/order1_kv1024_conventional/raw/compaction_breakdown.tsv`
- Comp 91B: `/home/smrc/virtual_compaction/vcomp/experiments/artifacts/log_loads/figure2_1tb_fourcell_260903_resume1/order3_kv91_conventional/raw/compaction_breakdown.tsv`
- VComp 1KB: `/home/smrc/virtual_compaction/vcomp-prof-job/experiments/artifacts/job_profile/full_1000gib_260913_01/r1_on/events.tsv`
- VComp 91B: `/home/smrc/virtual_compaction/vcomp-prof-job/experiments/artifacts/job_profile/full_1000gib_91b_260913_01/r1_on/events.tsv`

파일 SHA-256, 정확한 명령 경로, 측정 source commit/binary hash는 manifest.json에 있다. Baseline commit 33f5de2110084a37adc07fdc9928fd15db87ee6f/binary 4a807ef1113420b3029812b497f1bb5c7fb1f11b3f79a9838a0045da84709321, virtual source a966c0b0078153c8545696c6e811083f845a52b2/binary 552c55ff69046f917c86fe6fcbab8ff3c791ccecfd0d057350448572c50712da. Dirty profiling source snapshots/patches remain in each raw provenance.

`job_id`는 uint64 범위 문자열로 읽어야 하며 float/Excel 자동 변환으로 읽지 않는다. 예: pandas.read_csv(path, sep="\t", dtype={"job_id": "string"}). MiB=2^20 B, ms=10^-3 s. 다른 엔진에서 측정하지 않은 단계는 빈 칸이고 측정된 0과 구분한다.

재현 (vcomp/ 기준):

```bash
python3 experiments/analysis/export_comp_vcomp_input_scaling.py \
  --output-dir experiments/results/20260914-084200_comp_vcomp_job_scaling \
  --raw-output-dir experiments/artifacts/analysis/20260914-084200_comp_vcomp_job_scaling
```

`cumulative_job_seconds`는 병렬 worker들의 job latency 합이며 전체 로딩 wall time이 아니다. 작업별 TSV 압축본은 [jobs_with_breakdown.tsv.gz](../../artifacts/analysis/20260914-084200_comp_vcomp_job_scaling/jobs_with_breakdown.tsv.gz)에 있다.
