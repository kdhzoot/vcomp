# Membership 이전 Baseline 10개와 F2Load f01-f10 fidelity

저장된 결과를 재계산했습니다. 신규 로딩·YCSB·DB 스캔은 실행하지 않았습니다.
RocksDB 기반 비교이며, 입력 1000 GiB, KV 24+1000 B, 48 threads, 50 GiB cache, workload별 300초입니다.
Baseline은 b01,b02,b03,n01,n02,n03,n04,r01,r02,run3, F2Load는 최초 f01-f10입니다. 선별된 후속 10개 또는 membership 적용 e01-e10을 섞지 않았습니다.

## 평균과 변동 범위

각 arm을 동일 가중치로 평균했습니다. 범위는 최솟값–최댓값이며 신뢰구간이 아닙니다. 두 집합은 paired trial로 취급하지 않습니다.
서버 간섭이 기록된 f01도 주 비교(n=10)에 포함했습니다. f01 전체를 제외한 n=9 민감도 분석은 sensitivity_without_f01.tsv에 따로 제공합니다.

| 지표 | Baseline 평균 | F2Load 평균 | 차이 |
|---|---:|---:|---:|
| final_sst_count | 13.498K | 13.206K | -2.162% |
| final_sst_bytes | 823.908G | 823.092G | -0.099% |
| average_sst_bytes | 61.041M | 62.328M | +2.109% |

SST 총크기는 바이트 단위, 파일당 평균 크기는 각 arm의 bytes/count를 평균한 값입니다. K/M/G는 십진수입니다.
SST 수·총크기는 각 arm의 source_identity.json에서 계산했습니다. 모든 A–F에서 identity가 같고 C에서 쓰기·flush·compaction이 0임을 확인했습니다.
레벨별 파일 수는 정확한 정수이며 크기는 levelstats가 출력한 정수 MiB 정밀도입니다. 상세: structure_raw.tsv, levels_raw.tsv, levels_summary.tsv.

| Workload | Metric (unit) | Baseline mean [min, max] | F2Load mean [min, max] | Mean change |
|---|---|---:|---:|---:|
| A | throughput_ops_sec (ops/s) | 699.783K [689.211K, 724.412K] | 695.066K [681.749K, 706.421K] | -0.674% |
| A | avg_latency_us (us) | 68.6035 [66.2570, 69.6410] | 69.0627 [67.9450, 70.4020] | +0.669% |
| A | filter_checks_per_get (checks/Get) | 8.9315 [8.8587, 9.0019] | 8.8783 [8.7592, 8.9556] | -0.595% |
| A | positive_lookup_pct (%) | 86.7826 [86.7547, 86.8469] | 86.7518 [86.7068, 86.7876] | -0.0309 pp |
| A | bloom_false_positive_pct (%) | 0.9667 [0.9542, 0.9739] | 0.9619 [0.9573, 0.9678] | -0.0048 pp |
| A | data_cache_misses_per_op (misses/op) | 1.0872 [1.0655, 1.1000] | 1.1045 [1.0891, 1.1195] | +1.587% |
| A | data_cache_hit_pct (%) | 13.5244 [13.3784, 13.7490] | 13.4280 [13.2818, 13.5579] | -0.0964 pp |
| A | compaction_read_bytes (bytes) | 842.432G [817.877G, 883.388G] | 853.757G [824.022G, 881.325G] | +1.344% |
| A | compaction_write_bytes (bytes) | 786.225G [762.579G, 825.188G] | 797.412G [769.551G, 823.340G] | +1.423% |
| A | compaction_read_bytes_per_op (bytes/op) | 4.012K [3.918K, 4.064K] | 4.094K [4.028K, 4.158K] | +2.033% |
| A | compaction_write_bytes_per_op (bytes/op) | 3.744K [3.653K, 3.797K] | 3.824K [3.761K, 3.885K] | +2.112% |
| A | flush_write_bytes_per_op (bytes/op) | 354.8448 [354.7504, 354.9902] | 354.8868 [354.7910, 354.9742] | +0.012% |
| A | device_read_ios (I/Os) | 50.522M [49.677M, 52.506M] | 50.175M [49.070M, 51.168M] | -0.686% |
| A | device_write_ios (I/Os) | 1.791M [1.720M, 1.902M] | 1.870M [1.765M, 1.960M] | +4.406% |
| A | device_read_bytes (bytes) | 1359.220G [1328.376G, 1417.820G] | 1362.890G [1324.664G, 1397.729G] | +0.270% |
| A | device_write_bytes (bytes) | 886.812G [861.148G, 928.467G] | 901.866G [872.225G, 929.548G] | +1.698% |
| A | device_read_bytes_per_op (bytes/op) | 6.474K [6.364K, 6.523K] | 6.535K [6.474K, 6.603K] | +0.951% |
| A | device_write_bytes_per_op (bytes/op) | 4.224K [4.126K, 4.272K] | 4.324K [4.263K, 4.386K] | +2.389% |
| B | throughput_ops_sec (ops/s) | 1.041M [1.019M, 1.065M] | 1.044M [1.031M, 1.057M] | +0.277% |
| B | avg_latency_us (us) | 46.1161 [45.0600, 47.0960] | 45.9821 [45.4070, 46.5510] | -0.291% |
| B | filter_checks_per_get (checks/Get) | 4.1401 [4.0821, 4.2050] | 4.1514 [4.0976, 4.2549] | +0.273% |
| B | positive_lookup_pct (%) | 83.5182 [83.4836, 83.5562] | 83.5006 [83.4833, 83.5381] | -0.0175 pp |
| B | bloom_false_positive_pct (%) | 1.0275 [1.0111, 1.0414] | 1.0048 [0.9841, 1.0321] | -0.0227 pp |
| B | data_cache_misses_per_op (misses/op) | 0.3530 [0.3515, 0.3552] | 0.3575 [0.3541, 0.3598] | +1.259% |
| B | data_cache_hit_pct (%) | 45.6833 [45.5418, 45.8380] | 45.3316 [45.1027, 45.4236] | -0.3517 pp |
| B | compaction_read_bytes (bytes) | 167.417G [164.678G, 171.490G] | 174.462G [169.972G, 177.896G] | +4.208% |
| B | compaction_write_bytes (bytes) | 160.231G [157.620G, 164.115G] | 166.872G [162.358G, 170.579G] | +4.144% |
| B | compaction_read_bytes_per_op (bytes/op) | 536.0701 [529.2251, 545.1816] | 557.0192 [542.9201, 567.4209] | +3.908% |
| B | compaction_write_bytes_per_op (bytes/op) | 513.0614 [506.5442, 522.0239] | 532.7859 [518.5997, 544.0815] | +3.844% |
| B | flush_write_bytes_per_op (bytes/op) | 35.5465 [35.3727, 35.6679] | 35.5333 [35.3842, 35.6367] | -0.037% |
| B | device_read_ios (I/Os) | 82.091M [80.486M, 83.855M] | 82.277M [81.334M, 83.437M] | +0.226% |
| B | device_write_ios (I/Os) | 376.017K [359.970K, 403.668K] | 413.445K [396.127K, 433.028K] | +9.954% |
| B | device_read_bytes (bytes) | 872.316G [858.492G, 890.775G] | 879.810G [869.132G, 892.102G] | +0.859% |
| B | device_write_bytes (bytes) | 172.090G [169.089G, 176.916G] | 179.357G [175.294G, 182.756G] | +4.223% |
| B | device_read_bytes_per_op (bytes/op) | 2.793K [2.782K, 2.808K] | 2.809K [2.791K, 2.821K] | +0.576% |
| B | device_write_bytes_per_op (bytes/op) | 551.0252 [543.3998, 559.0402] | 572.6499 [559.9204, 582.9237] | +3.924% |
| C | throughput_ops_sec (ops/s) | 1.601M [1.511M, 1.643M] | 1.470M [1.143M, 1.645M] | -8.171% |
| C | avg_latency_us (us) | 29.9973 [29.2150, 31.7640] | 33.0050 [29.1840, 41.9920] | +10.027% |
| C | filter_checks_per_get (checks/Get) | 3.6098 [3.3177, 4.0253] | 3.8725 [3.2447, 4.2468] | +7.278% |
| C | positive_lookup_pct (%) | 60.2923 [60.2920, 60.2927] | 63.5538 [61.6859, 66.9385] | +3.2615 pp |
| C | bloom_false_positive_pct (%) | 0.8636 [0.7436, 1.0945] | 0.9091 [0.7175, 1.3065] | +0.0455 pp |
| C | data_cache_misses_per_op (misses/op) | 0.2344 [0.2341, 0.2346] | 0.2345 [0.2339, 0.2354] | +0.036% |
| C | data_cache_hit_pct (%) | 62.7951 [62.4968, 63.4572] | 64.7781 [63.5019, 67.4876] | +1.9830 pp |
| C | compaction_read_bytes (bytes) | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | N/A |
| C | compaction_write_bytes (bytes) | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | N/A |
| C | compaction_read_bytes_per_op (bytes/op) | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | N/A |
| C | compaction_write_bytes_per_op (bytes/op) | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | N/A |
| C | flush_write_bytes_per_op (bytes/op) | 0.0000 [0.0000, 0.0000] | 0.0000 [0.0000, 0.0000] | N/A |
| C | device_read_ios (I/Os) | 113.097M [106.716M, 115.986M] | 103.891M [81.097M, 116.020M] | -8.140% |
| C | device_write_ios (I/Os) | 41.3000 [37.0000, 47.0000] | 43.6000 [38.0000, 54.0000] | +5.569% |
| C | device_read_bytes (bytes) | 939.636G [887.231G, 963.361G] | 863.499G [676.325G, 963.097G] | -8.103% |
| C | device_write_bytes (bytes) | 1.883M [1.872M, 1.901M] | 1.578M [1.544M, 1.618M] | -16.203% |
| C | device_read_bytes_per_op (bytes/op) | 1.956K [1.953K, 1.958K] | 1.958K [1.950K, 1.972K] | +0.100% |
| C | device_write_bytes_per_op (bytes/op) | 0.0039 [0.0038, 0.0041] | 0.0036 [0.0032, 0.0047] | -7.811% |
| D | throughput_ops_sec (ops/s) | 2.670M [2.625M, 2.703M] | 2.657M [2.612M, 2.731M] | -0.479% |
| D | avg_latency_us (us) | 17.9777 [17.7550, 18.2850] | 18.0661 [17.5730, 18.3790] | +0.492% |
| D | filter_checks_per_get (checks/Get) | 1.0778 [0.9909, 1.1760] | 1.0866 [0.9043, 1.1790] | +0.809% |
| D | positive_lookup_pct (%) | 92.0738 [92.0427, 92.0964] | 91.9929 [91.9424, 92.0387] | -0.0809 pp |
| D | bloom_false_positive_pct (%) | 0.9587 [0.8604, 1.0245] | 0.9833 [0.7998, 1.1188] | +0.0245 pp |
| D | data_cache_misses_per_op (misses/op) | 0.1518 [0.1516, 0.1519] | 0.1514 [0.1511, 0.1517] | -0.250% |
| D | data_cache_hit_pct (%) | 62.9291 [62.7737, 63.1451] | 62.8939 [62.7313, 62.9950] | -0.0352 pp |
| D | compaction_read_bytes (bytes) | 175.822M [0.0000, 760.908M] | 217.900M [0.0000, 820.510M] | +23.932% |
| D | compaction_write_bytes (bytes) | 169.315M [0.0000, 803.869M] | 239.554M [0.0000, 881.631M] | +41.484% |
| D | compaction_read_bytes_per_op (bytes/op) | 0.2187 [0.0000, 0.9416] | 0.2727 [0.0000, 1.0305] | +24.672% |
| D | compaction_write_bytes_per_op (bytes/op) | 0.2106 [0.0000, 0.9947] | 0.3001 [0.0000, 1.1072] | +42.503% |
| D | flush_write_bytes_per_op (bytes/op) | 51.9680 [51.9454, 52.0021] | 51.9857 [51.9328, 52.0096] | +0.034% |
| D | device_read_ios (I/Os) | 122.059M [119.931M, 123.538M] | 121.171M [118.937M, 124.475M] | -0.728% |
| D | device_write_ios (I/Os) | 89.692K [82.420K, 94.761K] | 87.048K [82.877K, 89.679K] | -2.947% |
| D | device_read_bytes (bytes) | 1013.656G [996.154G, 1025.658G] | 1005.912G [987.361G, 1033.350G] | -0.764% |
| D | device_write_bytes (bytes) | 41.877G [41.120G, 42.877G] | 41.745G [40.917G, 43.210G] | -0.314% |
| D | device_read_bytes_per_op (bytes/op) | 1.265K [1.265K, 1.267K] | 1.262K [1.259K, 1.265K] | -0.286% |
| D | device_write_bytes_per_op (bytes/op) | 52.2751 [52.0583, 53.0568] | 52.3624 [52.0559, 53.1646] | +0.167% |
| E | throughput_ops_sec (ops/s) | 106.575K [105.414K, 107.067K] | 106.035K [103.732K, 107.058K] | -0.507% |
| E | avg_latency_us (us) | 450.3728 [448.2980, 455.3210] | 452.6892 [448.3370, 462.7080] | +0.514% |
| E | data_cache_misses_per_op (misses/op) | 9.0040 [8.9945, 9.0168] | 8.9956 [8.9501, 9.0327] | -0.093% |
| E | data_cache_hit_pct (%) | 55.8283 [55.3446, 56.2287] | 55.9626 [54.9453, 56.2505] | +0.1344 pp |
| E | compaction_read_bytes (bytes) | 71.382M [0.0000, 713.824M] | 215.950M [0.0000, 888.532M] | +202.526% |
| E | compaction_write_bytes (bytes) | 68.606M [0.0000, 686.055M] | 214.719M [0.0000, 884.842M] | +212.976% |
| E | compaction_read_bytes_per_op (bytes/op) | 2.2278 [0.0000, 22.2776] | 6.7768 [0.0000, 27.9749] | +204.199% |
| E | compaction_write_bytes_per_op (bytes/op) | 2.1411 [0.0000, 21.4110] | 6.7382 [0.0000, 27.8587] | +214.707% |
| E | flush_write_bytes_per_op (bytes/op) | 50.9440 [50.3494, 51.9811] | 50.9966 [50.1354, 52.0467] | +0.103% |
| E | device_read_ios (I/Os) | 144.088M [142.373M, 144.923M] | 143.531M [141.048M, 144.518M] | -0.387% |
| E | device_write_ios (I/Os) | 3.728K [2.721K, 5.149K] | 3.861K [2.382K, 5.682K] | +3.573% |
| E | device_read_bytes (bytes) | 2065.772G [2042.167G, 2076.494G] | 2051.847G [2011.997G, 2066.917G] | -0.674% |
| E | device_write_bytes (bytes) | 1.737G [1.650G, 2.358G] | 1.874G [1.623G, 2.542G] | +7.920% |
| E | device_read_bytes_per_op (bytes/op) | 64.605K [64.534K, 64.718K] | 64.497K [64.056K, 64.803K] | -0.167% |
| E | device_write_bytes_per_op (bytes/op) | 54.3088 [52.1627, 73.5904] | 58.9023 [52.1544, 80.0374] | +8.458% |
| F | throughput_ops_sec (ops/s) | 604.600K [599.723K, 610.443K] | 598.979K [594.417K, 606.675K] | -0.930% |
| F | avg_latency_us (us) | 79.3896 [78.6260, 80.0320] | 80.1348 [79.1180, 80.7490] | +0.939% |
| F | filter_checks_per_get (checks/Get) | 7.0606 [6.9531, 7.2439] | 7.0111 [6.7963, 7.3706] | -0.700% |
| F | positive_lookup_pct (%) | 86.5200 [86.5059, 86.5374] | 86.4822 [86.4637, 86.4963] | -0.0378 pp |
| F | bloom_false_positive_pct (%) | 0.9737 [0.9655, 0.9943] | 0.9698 [0.9630, 0.9892] | -0.0039 pp |
| F | data_cache_misses_per_op (misses/op) | 1.3294 [1.3184, 1.3396] | 1.3425 [1.3245, 1.3554] | +0.987% |
| F | data_cache_hit_pct (%) | 19.2728 [19.1137, 19.4839] | 19.1442 [18.9657, 19.4373] | -0.1286 pp |
| F | compaction_read_bytes (bytes) | 807.417G [794.150G, 818.544G] | 811.565G [796.616G, 821.862G] | +0.514% |
| F | compaction_write_bytes (bytes) | 758.388G [745.605G, 768.974G] | 762.610G [748.252G, 771.796G] | +0.557% |
| F | compaction_read_bytes_per_op (bytes/op) | 4.451K [4.407K, 4.494K] | 4.516K [4.439K, 4.568K] | +1.457% |
| F | compaction_write_bytes_per_op (bytes/op) | 4.181K [4.137K, 4.222K] | 4.244K [4.170K, 4.295K] | +1.501% |
| F | flush_write_bytes_per_op (bytes/op) | 354.9219 [354.7957, 355.1209] | 354.9154 [354.8128, 355.1504] | -0.002% |
| F | device_read_ios (I/Os) | 81.512M [80.765M, 82.208M] | 80.754M [80.072M, 81.888M] | -0.930% |
| F | device_write_ios (I/Os) | 1.846M [1.784M, 1.895M] | 1.943M [1.858M, 1.989M] | +5.282% |
| F | device_read_bytes (bytes) | 1565.142G [1544.312G, 1578.009G] | 1557.208G [1536.402G, 1575.119G] | -0.507% |
| F | device_write_bytes (bytes) | 836.829G [826.200G, 846.084G] | 843.815G [829.920G, 854.824G] | +0.835% |
| F | device_read_bytes_per_op (bytes/op) | 8.628K [8.583K, 8.712K] | 8.665K [8.562K, 8.715K] | +0.427% |
| F | device_write_bytes_per_op (bytes/op) | 4.613K [4.569K, 4.645K] | 4.695K [4.625K, 4.744K] | +1.781% |

## 지표 정의와 해석 범위

- filter_checks_per_get = (bloom.filter.useful + bloom.filter.full.positive) / number.keys.read. 분모는 RMW 내부 Get을 포함한 실제 Get 수입니다. 예전 paper_ch3_band25_raw.tsv의 filter_checks_per_lookup은 filter-cache accesses/전체 작업 수였으므로 혼용하지 않습니다.
- positive_lookup_pct = successful_gets / number.keys.read × 100. Bloom positive 비율과 다릅니다. 전체 키 집합 recall도 아닙니다. E는 Get이 없어 조회당 지표를 비워 두었습니다.
- bloom_false_positive_pct = (full.positive − full.true.positive) / (useful + full.positive − full.true.positive) × 100. 키가 없는 SST를 검사했을 때의 Bloom 오탐률입니다.
- compaction_*_bytes는 YCSB 실행 중 엔진 compaction ticker입니다. per_op의 분모는 YCSB 작업 수입니다. 로딩 누적 쓰기량·WAF가 아닙니다. D/E의 컴팩션량은 매우 작아 상대 변화율만으로 차이를 해석하지 않습니다.
- data_cache_misses_per_op는 전체 작업당 데이터 블록 캐시 miss이며 E의 scan, F의 RMW와 엔진 background 활동도 포함할 수 있습니다. 데이터 캐시 hit 비율은 hit/(hit+miss)입니다.
- device 지표는 md0의 diskstats.end − diskstats.start이며 sector는 512 B입니다. Get 횟수 또는 엔진 SST bytes와 다릅니다. open/close 및 다른 host I/O가 포함된 프로세스 구간입니다.
- avg_latency_us는 각 db_bench 실행의 평균 작업 지연시간입니다. operation_latency_raw.tsv는 작업 종류별 원본 평균/p50/p99를 보존하며 10개 histogram을 합친 percentile을 주장하지 않습니다.
- 모든 120개 full cell에서 성공, 48 threads, 같은 workload별 옵션·측정 바이너리를 확인했습니다. f01-f05의 별도 clean settle과 f06-f10의 in-process waitforcompaction, Baseline byte copy와 F2Load fresh load의 준비 절차 차이는 남습니다.
- 예전 loads.json의 일부 로딩/I/O 값은 stale template이므로 사용하지 않았습니다. 이 보고서는 그 파일로 10개 평균 로딩 시간/WAF를 계산하지 않습니다.
- baseline 범위 안에 평균이 있다는 사실만으로 통계적 동등성이나 키 집합 일치를 입증하지 않습니다. 각 run의 세부 원본은 behavior_raw.tsv와 source_manifest.json에서 추적할 수 있습니다.

## 키 집합 직접 비교: 별도 n=1

이 값은 baseline run3와 F2Load f06의 한 쌍이며 10개 평균이 아닙니다. 기존 스캔 bitmap의 popcount/교집합/합집합을 다시 계산했고 RESULTS.md §9.1과 일치했습니다.
Bitmap과 DB의 대응은 문서·파일명에 근거하며 최초 scanner 실행 명령은 찾지 못했습니다. 원본 경로·hash·계산은 keyset_f06_audit.json에 보존했습니다.

| 지표 | 값 |
|---|---:|
| baseline_unique_keys | 662.840M |
| f2load_unique_keys | 662.463M |
| intersection_keys | 418.753M |
| missing_keys | 244.087M |
| invented_keys | 243.710M |
| recall_pct | 63.1756 |
| precision_pct | 63.2116 |
| jaccard | 0.4619 |

## 재현

```bash
python3 experiments/analysis/export_pre_membership_fidelity.py \
  --output-dir experiments/results/20260914-072910_pre_membership_fidelity \
  --keyset-audit experiments/results/20260914-072910_pre_membership_fidelity/keyset_f06_audit.json
```
