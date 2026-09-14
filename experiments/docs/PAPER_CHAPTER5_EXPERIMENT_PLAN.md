# Chapter 5 experiment plan

작성일: 2026-09-13. 상태: **실험 설계 완료, 새 실행 미착수**.

이 문서는 저자와 합의한 7개 subsection의 실험, 주장, 표현 방법, 실행 조건을 정의한다.
기존 `PAPER_EVALUATION_PLAN.md`의 과거 실행 기록은 보존한다. 새 Chapter 5 캠페인에서는
이 문서가 우선하며, 과거 실행의 완료 여부나 수치를 새 계획의 완료로 취급하지 않는다.
실행 명령과 최종 binary/configuration manifest는 pilot 준비 단계에서 확정한다.
현재 runner가 이 계획 전체를 이미 지원한다는 의미는 아니다.

## 0. 전체 구성과 실행 원칙

| 절 | 검증 질문 | 필요한 실험 | 주요 표현 |
|---|---|---|---|
| 5.1 Loading Performance | 얼마나 빠르며, 어떤 작업 비용이 줄었는가? | 기존 loading 비교, flush/registration 비교, compaction/virtual compaction 비교, breakdown | 실제 시간 막대, job별 점, 단계 시간 분해, disk-write 표 |
| 5.2 Fidelity | 동일 설정에서 최종 구조와 workload behavior를 재현하는가? | 두 방법 각각 독립 로딩 10회, 각 DB에서 YCSB A–F | DB별 점, 레벨별 구조, 별도 I/O figure |
| 5.3 Flexibility | 설정이 바뀌어도 해당 baseline을 재현하는가? | 7개 설정 축과 대표 조합 | 설정별 baseline/F2Load 실제 수치 |
| 5.4 Virtual Compaction Accuracy | 동일 입력의 compaction 출력을 정확히 예측하는가? | ATC와 같은 real-input 비교 재실행 | actual/predicted 점, 오차 CDF |
| 5.5 Memory Overhead | 기본 파라미터의 메모리 비용은 얼마인가? | 규모/KV별 peak RSS와 descriptor 구성 | 실제 메모리 곡선, 구성별 메모리 |
| 5.6 Parameter Sensitivity | PLR/KMV 선택이 비용과 정확도를 어떻게 바꾸는가? | error bound와 sketch size sweep | 파라미터별 비용/오차 곡선 |
| 5.7 Portability | 다른 LSM 기반 KV store에서도 적용되는가? | 대상 store 포팅, 자체 baseline과 loading/fidelity 비교 | 변경 범위 표, 성능/구조 비교 |

- 5.1의 작업 비교와 breakdown은 비용 분석이다. 구성 요소를 제거한 변형을 비교하는
  ablation study로 부르지 않는다. 별도 ablation은 필수 범위에 넣지 않는다.
- ADOC/BlobDB 비교, compaction priority 전체 sweep, filter 형식 전체 sweep은 이번 필수 범위 밖이다.
- 기존 DB, 원본 결과, dirty worktree를 보존한다. 로딩 DB를 자동 삭제하지 않는다.
  workload 사본도 기본적으로 보존하며, 공간 부족을 이유로 기존 runner의 자동 삭제를 실행하지 않는다.
- 한 storage server에서 경쟁하는 로딩/YCSB/profiling을 동시에 실행하지 않는다.
- 저자는 지금 실험 계획을 요청했다. 이 문서 작성으로 장시간 실험을 시작하지 않는다.

### 공통 기준 설정과 단위

P0의 제안 기준은 기존 비교와 연결되는 1000 GiB logical input, 24 B key + 1000 B value,
64 MiB memtable, 64 MiB target SST, 256 MiB level base, multiplier 10, compression off이다.
KV 합계는 1024 B이며 record 수는 1,048,576,000이다. 기준 uniqueness는 uniform sampling
with replacement의 관측값을 기록한다. 63.2%를 모든 generator의 보장값으로 간주하지 않는다.

- 논문에서 관행적으로 사용한 1/2/4/8 TB point는 기존 harness에서
  1000/2000/4000/8000 GiB였는지 run별로 확인한다. 새 matrix의 제안도 이 GiB 값이다.
  manifest에는 정확한 input bytes를 저장하고, figure의 최종 단위는 실제 bytes에 맞춘다.
  1000 GiB를 정확히 1 decimal TB라고 쓰지 않는다.
- 새 KV 축은 총 100/1024/10240 B를 제안한다. key 24 B 고정 시 value는 76/1000/10216 B이다.
  91 B 과거 실험은 별도 설정으로 유지한다. 작은 KV의 key/value 비율까지 기존 91 B와 같다고 하지 않는다.
- dataset-size 고정 축에서는 N=floor(input_bytes/KV_bytes)로 정의하고 실제 N*KV_bytes를 보고한다.
- leveled compaction, priority, num_levels, dynamic-level sizing, L0 triggers, pending limits,
  memtable representation, 16 write buffers, WAL, direct I/O, SST format, filter/index options,
  compression entropy, thread count를 manifest로 고정한다. 기존 캠페인 값은 자동 상속하지 않는다.
- 제안 병렬도는 background jobs 48, subcompactions 1, phase1 shards 8,
  materialization workers 48이다. pilot에서 지원 여부와 effective options를 확인한다.
- 계측용 profiler와 성능용 release build는 별도로 동결하고 hash와 source diff를 기록한다.
  baseline과 F2Load의 공통 옵션 및 불가피한 구현/버전 차이를 공개한다.

### 완료, 복사, 측정 경계

1. Loader의 입력 처리, virtual drain, materialization, 요구되는 flush/completion을 모두 기록한다.
   종료 코드뿐 아니라 running jobs, pending work, 파일 설치, reopen 가능 여부를 확인한다.
   estimated pending bytes=0 하나만으로 완료를 판정하지 않는다.
2. 실험적으로 요구되는 추가 physical compaction은 로딩 시간과 disk writes에 포함한다.
   모든 레벨을 강제로 최적화하는 full compaction을 완료 대기로 대체하지 않는다.
3. 완성된 source DB는 immutable로 보존하고 각 workload는 새로운 **physical deep copy**에서 실행한다.
   SST hard link/reflink만으로 원래 placement가 제거되었다고 주장하지 않는다.
   동일 파일 순서/복사 방식, copy 후 sync, cache 초기화, warm-up 정책을 양쪽에 적용한다.
4. 복사/검증 I/O는 workload 측정 전에 끝내고 counter baseline을 다시 잡는다.
   원본 DB의 identity와 옵션은 복사 전후 검증한다.
5. Foreground throughput은 workload 구간으로 계산한다. 이후 drain 시간과 I/O는 별도로 기록한다.
   read/write bytes 비교는 같은 요청 수·쓰기 입력을 기준으로 하며 drain 포함 범위를 명시한다.
6. 3장 고정-A는 210,000,000 ops, 48 threads, seed 87654321, 50 GiB cache,
   duration=0, workload 후 waitforcompaction이며 명시적 memtable flush는 없었다.
   같은 비교를 재사용할 때 이를 그대로 유지하고 실제 read/update count도 맞는지 확인한다.
   약 100 GiB라는 목표를 새로운 exact-byte stopping rule로 조용히 바꾸지 않는다.

### 공통 지표 정의

- loading_seconds: 명시한 loading 시작부터 공통 완료 시점까지의 wall time.
- foreground_ops_per_sec: 실제 완료 operation 수 / foreground seconds.
- mean latency: operation 종류별 count/sum에서 계산. throughput 역수를 48-thread 요청 latency로 쓰지 않는다.
- filter_checks_per_lookup: 검증한 point-lookup filter counter / 해당 point lookup 수.
  모든 YCSB operation 수나 positive filter 수를 분모로 사용하지 않는다.
- positive_lookup_pct: 성공한 point lookup 수 / 전체 point lookup 수 * 100.
  YCSB F의 내부 Get 포함 여부를 명시하고 scan은 별도로 처리한다.
- compaction_write_bytes: foreground 및 합의한 drain 동안 완료된 compaction output bytes.
  flush/WAL/device writes와 구분한다. compaction read bytes도 원시 데이터에 보존한다.
- device read/write counts/bytes: 동일 block-device 계층에서 구간별 delta 측정.
  RAID와 구성 SSD 값을 함께 더하지 않는다. 누락 counter는 0이 아니라 missing으로 저장한다.
- 구조: final SST bytes, populated levels, per-level bytes/count, SST size,
  key-range placement/overlap. 동일 key 표본으로 level별 candidate count도 비교한다.
- CPU seconds, cumulative job wall seconds, elapsed wall seconds는 서로 다른 지표다.

## 1. Loading Performance

### L1. End-to-end loading speedup

**주장:** intermediate SST construction을 생략해 전체 loading time을 줄인다.

- 기존 fillrandom/F2Load scaling 결과를 우선 재사용한다. 각 point의 KV size, bytes,
  binary, buffer 옵션, completion boundary가 비교 가능한지 audit한다.
- historical 91 B의 2-buffer baseline과 새 16-buffer baseline을 섞어 하나의 speedup 곡선을 만들지 않는다.
- measured/projected를 필드로 구분한다. 46.2 h/47 h, 47.9x, 96.6%는 원본 근거 확인 후 사용한다.
- 수집: loading_seconds, 실제 input/final SST bytes, device write bytes, SST write bytes,
  반복 수와 원본 run ID. 실제 device counter가 없으면 final DB size를 device writes로 대체하지 않는다.
- 표현: KV별 패널, x=dataset size, y=loading time(0 시작), 방법별 막대와 speedup 주석.
  작은 F2Load 막대에는 실제 시간도 기입한다. 총 disk writes는 인접 표에 함께 제시한다.
- 완료: 사용 point 모두 manifest와 원시 근거가 연결되고, 추정값이 측정값으로 표시되지 않는다.

### L2. Flush vs. vSST preparation and registration

**주장:** 실제 L0 SST 생성 대신 vSST를 구성·등록하는 경로의 비용이 작다.

- 입력 logical bytes뿐 아니라 record 수, unique 수, key/value 크기와 순서를 맞춘 batch fixture를 사용한다.
  제안 batch 크기는 16/64/256 MiB, KV는 100/1024/10240 B이다. 각 cell 최소 10개 batch를 측정한다.
- baseline 경계는 선택된 immutable input에서 FlushJob 완료/Version 설치까지다.
  memtable에 도달하기 전의 insert 비용은 이 경계 밖이라고 명시한다.
- F2Load는 (a) batch 준비 이후 sort/dedup/PLR/KMV/descriptor build,
  (b) 등록 호출, (c) Version 설치/스케줄링을 분리한다. 공통 시작 상태를 설명하고,
  준비 비용을 포함한 L0-ready 시간도 별도로 보고한다.
- 같은 unsorted records를 양쪽에 준비하는 비용은 별도 기록한다. baseline VectorRep의 lazy sort는
  실제 flush 경계 안에서 발생할 수 있으므로 반드시 포함한다.
- 순수 registration 호출 시간과 준비 포함 시간 둘 다 제시한다. 순수 호출만 flush 전체와 비교해
  end-to-end speedup처럼 해석하지 않는다.
- F2Load registration batching을 유지하고 batch당 file/record 수를 기록한다.
  여러 vSST 등록 호출 하나와 SST 하나를 단순히 동일 job으로 비교하지 않는다.
- 수집: start/end, input records/bytes, unique count, phase별 elapsed, registration files/batch,
  lock wait, LogAndApply, install, CPU time(계측 가능 시), output bytes/files.
- 표현: batch 크기별 실제 시간 점/막대. preparation과 registration을 분리 표시하되
  중첩/포함 관계가 있는 timer를 합산하지 않는다.
- pilot: 1–4 GiB fixture로 sort 포함 여부, 등록 완료, 중복 카운터, 파일 수를 검증한다.
  계측 on/off 반복을 교대로 실행해 overhead를 기록하고 계측값을 release 성능과 혼합하지 않는다.

### L3. Real compaction vs. virtual compaction

**주장:** 같은 compaction 입력에서 실제 KV merge/rewrite 대신 descriptor를 처리해 시간을 줄인다.

- 5.4의 real-input capture/replay를 재사용한다. compaction ID, input SST 집합,
  target output size/level, grandparent boundaries, options가 같은 job끼리 대응시킨다.
- 실제 compaction 시간은 capture용 input/output 스캔을 제외한 경계로 수집한다.
  capture 이후 cache가 따뜻해진 실제 compaction을 기존 cold run과 섞지 않는다.
- virtual timing은 descriptor가 이미 존재하는 상태의 merge/split 실행과 metadata commit을 구분한다.
  실험용 descriptor 구축·파일 읽기 시간도 별도 저장해 제외 이유를 밝힌다.
- 기존 capture의 사후 스캔이 전체 loading 성능을 교란할 수 있으므로 L1은 release 실행을 쓴다.
- virtual job은 같은 descriptor의 private copy로 10회 재실행하여 timer 해상도와 변동을 확인한다.
  동일 job 재실행을 독립적으로 로딩한 DB 10회로 세지 않는다.
- 표본은 결과를 보기 전에 level transition과 input 규모로 층화 선정한다.
  trivial moves는 rewriting compaction과 분리하고 unsupported job/누락 수를 보고한다.
- 표현: x=입력 bytes 또는 records, y=실제 job time, 동일 job의 actual/virtual 점.
  규모 차이가 크면 선형 small multiples를 먼저 사용하고 log 축은 명시적으로 표시한다.
  median job speedup과 전체 speedup은 다른 통계라고 명시한다.

### L4. F2Load loading breakdown

**주장:** 가속화 후 전체 loading time을 지배하는 작업과 규모에 따른 변화가 무엇인지 설명한다.

- 5.3 규모 축의 F2Load 실행에서 함께 수집한다. 별도 DB를 중복 로딩하지 않는다.
- wall-time 분해: foreground phase1, foreground 종료 후 background drain,
  final materialization, 추가 physical completion/close. 시작·끝 timestamp를 보존한다.
- 작업 분해: generation, radix sort, PLR/KMV, registration, virtual merge/split,
  metadata commit/lock wait, SST materialization. RSS도 동시에 샘플링한다.
- phase1 동안 background virtual compaction이 진행되므로 bg_wait를 virtual compaction 총 시간으로 쓰지 않는다.
  작업별 누적 시간은 end-to-end phase 합계와 별도 표에 둔다.
- 표현: 순차 wall phase는 stacked bar, 대표 실행의 겹치는 작업은 timeline;
  내부 누적 작업 시간은 별도 표. Input size별 병목 변화를 해석한다.
- 검증: 순차 phase 합계와 정의한 총 시간의 차이를 기록하고, 누락 구간을 Other로 설명한다.
  측정 경계 밖 비용이나 residual을 임의로 key generation/CPU 시간에 배정하지 않는다.

## 2. Fidelity

**주장:** baseline 자체의 반복 변동을 고려해 최종 구조와 read/write behavior를 재현한다.

### F1. 독립 로딩과 구조

- P0에서 baseline 10개, F2Load 10개를 독립 로딩한다. DB ID는 base01–base10, f2load01–f2load10.
- 동일 load seed를 기본으로 하여 동일 설정에서의 실행/스케줄링 변동을 평가한다.
  같은 seed만으로 서로 다른 generator의 key sequence가 동일하다고 주장하지 않는다.
- 입력 분포, 관측 uniqueness, key membership 차이를 따로 기록한다. key sequence 동일성을 요구하는
  제어 실험은 deterministic stream/hash로 검증한다. 최신 구현을 결과 보고 중 바꾸지 않는다.
- 로딩 완료 후 구조 지표 수집. 범위 overlap과 candidate 수를 같은 key 표본에서 계산한다.
- 기존 DB는 manifest/build/옵션/완료 조건/보존 상태가 맞을 때만 10개에 포함한다.
  다른 F2Load binary의 f01–f15를 숫자만 맞추려고 합치지 않는다.
- 표현: 전체 size/count는 DB별 점, per-level bytes/count는 두 방법의 10회 분포,
  대표 배치는 평균에 가까운 DB를 사전 규칙으로 선택하고 선택 ID를 공개한다.

### F2. YCSB A–F

- 원본 20개 × 6 workload = 기본 120개 full cell. 각 cell은 새로운 physical deep copy에서 시작한다.
- 48 client threads, P0에서는 50 GiB cache, query seed 87654321을 제안 기준으로 한다.
  A/B/C/E/F Zipfian, D latest 등 실제 runner의 workload 정의와 read/write/scan/RMW mix를 동결한다.
- primary 비교는 fixed-work이다. A는 3장과 같은 210,000,000 ops 프로토콜을 우선 사용한다.
  B–F는 pilot baseline throughput으로 약 300초 규모가 되는 operation budget을 산출해
  full 실행 전에 고정한다. F2Load의 관측 속도에 따라 budget을 바꾸지 않는다.
- A–F의 실제 read/write/scan count를 확인하고 양쪽의 요청 입력을 맞춘다. threaded request order가
  완전히 같지 않으면 seed와 operation mix만으로 전역 write 순서까지 같다고 주장하지 않는다.
- 쓰기 workload는 종료 후 pending compaction을 drain하고 foreground/drain counters를 모두 수집한다.
  강제 flush 여부는 명시한다. A의 3장 재현에서는 임의의 추가 flush를 넣지 않는다.
- warm-up은 원본/복사 상태와 함께 고정한다. 쓰기 warm-up으로 초기 상태를 달리 만들지 않는다.
- 기존 300초 실행은 timed protocol의 보조 결과로만 재사용한다. fixed-work+drain 결과로 이름을 바꾸지 않는다.
- run order는 방법을 교대로 실행하고 workload 순서를 회전한다. 하나의 DB를 로딩하고 측정한 뒤
  다음 DB로 진행하며, 동시 competing I/O를 피한다.
- 지표: throughput, operation별 mean latency, filter checks/lookup, positive lookups,
  compaction write bytes, device read/write counts/bytes. E의 scan 지표는 point lookup과 분리한다.
- 표현: 원수치 dot plot; base1…base10, f2load1…f2load10; y축은 가능한 0부터,
  positive lookups는 0–100%. device I/O는 별도 figure. p99 패널은 추가하지 않는다.
- 해석: baseline 반복 범위와 방법 간 차이를 함께 보고한다. CI overlap을 equivalence 증명으로
  사용하지 않는다. 성공률 차이가 throughput 차이를 설명할 수 있는지도 확인한다.
- 완료: 20개 source manifest와 유효한 120개 full cell. 실패/재시도는 숨기지 않고 전부 기록한다.

## 3. Flexibility

**주장:** loading configuration마다 그 설정의 baseline에 대응하는 구조와 동작을 재현한다.

| 축 | 제안 값 | 고정/주의 사항 |
|---|---|---|
| KV size | 100/1024/10240 B | logical input 1000 GiB 고정, record 수 변화 |
| Dataset size | 1000/2000/4000/8000 GiB | KV 1024 B 고정 |
| Key uniqueness | 25/50/75/100% | inserted records 대비 unique keys; 관측값 검증 |
| Compression | none/LZ4 | key/value 내용과 entropy 고정, codec version 기록 |
| Level base | 64/256/1024 MiB | multiplier 10 고정 |
| Level multiplier | 4/10/20 | base 256 MiB 고정 |
| Target SST | 16/64/256 MiB | memtable 64 MiB 고정; 두 옵션 동시 sweep 아님 |

- P0 공유 시 위 축은 17개의 서로 다른 설정이다. 대표 조합 3개를 더해 총 20개 설정을 제안한다.
- 조합 J1: 100 B + uniqueness 25%; J2: LZ4 + base 64 MiB + multiplier 4;
  J3: input 8000 GiB + target SST 256 MiB. 나머지는 P0와 같다.
- 같은 설정별 baseline/F2Load 1쌍이 기본이며 P0는 Fidelity의 반복 DB를 재사용한다.
  따라서 P0를 제외하면 우선 19쌍(38개 source DB)이 필요하다. 메모리/시간 계측을 함께 수행한다.
- 각 DB에서 fresh copy YCSB C와 A: 20설정 × 2방법 × 2workload = 대표 비교 80 cell.
  P0의 대응 cell 4개는 Fidelity에서 공유한다. 나머지 설정은 단일 로딩 결과라는 한계를 표시한다.
- 중요한 mismatch가 나오면 해당 설정을 양쪽 추가 2회 반복한다. 기존 불일치를 버리고
  baseline에 가까운 DB만 선택하지 않는다. 재현되는 불일치는 limitation/수정 대상으로 기록한다.
- C는 frozen request budget, A는 해당 input의 약 10% logical writes를 주는 deterministic
  operation budget을 full 전에 계산한다. pair 안에서 실제 update count가 같아야 한다.
- 구조, throughput, mean latency, filter checks, positive lookups, compaction write bytes를 비교한다.
- 표현: 축별 small multiples의 실제 값, 구조 표와 behavior figure를 구분한다.
  단위가 다른 지표를 normalized heatmap 하나로 합치지 않는다.
- uniqueness 75/100%는 단순 fillrandom으로 보장되지 않는다. 동일 generator/stream을 두 경로가
  지원하는지 pilot에서 확인하고 observed cardinality와 stream hash를 저장한다.
  기존 trace 경로가 특정 비율을 지원한다는 오래된 문서를 그대로 실행 가능성으로 해석하지 않는다.
- compression은 entropy와 physical size model을 함께 검증한다. 모델 calibration 비용은 loading에 포함한다.
- num_levels와 capacity 설정은 고정하되 최대 레벨 포화 여부를 기록한다. 조용히 레벨 수를 늘리지 않는다.

## 4. Virtual Compaction Accuracy

**주장:** 동일 real compaction 입력에 대해 output SST count/bytes와 dedup cardinality를 예측한다.

- ATC와 같은 per-job 비교를 현재 구현에서 재실행한다. 5.1 L3와 capture 자료를 공유한다.
- 참고 구현: `scripts/trace/run_real_input_accuracy.py`, `tools/virtual_compaction_replay.cc`;
  상세 경계/제약: `REAL_INPUT_COMPACTION_ACCURACY.md`.
- capture는 실제 input/output key streams, file grouping, byte sizes, output level/target size,
  grandparent boundaries를 저장한다. capture scans는 real compaction timer 밖에 둔다.
- 4 GiB pilot에서 exact union, sortedness, padding, unsupported cases 검출을 확인한다.
  이후 대표 100 GiB capture로 확장하고 level coverage가 부족하면 계획된 대형 baseline에
  한정 capture를 추가한다. 모든 intermediate SST를 무제한 보관하는 전체 8 TB capture는 하지 않는다.
- capture budget(최대 jobs/bytes)은 pilot output bytes/job으로 계산해 실행 전에 동결한다.
- 지표: actual/predicted output files, signed count error, relative output-byte error,
  cardinality error. 실제 count/bytes=0인 경우 relative error 분모 규칙을 명시한다.
- 표현: 실제 count 대 예측 count 점(y=x), size error CDF, cardinality error CDF/표.
  level별 표본 수와 exact/±1 file 비율을 함께 보고한다. 극단 오차도 남긴다.
- 실제 입력에서의 정확도는 누적 예측 정확도의 증명이 아니다. 누적 결과는 5.2/5.3이 검증한다.
  필요하면 진행률 25/50/75/100% 구조 checkpoint를 추가하되 현재 필수 workload를 늘리지 않는다.
- 완료: capture/replay lineage와 supported/unsupported/failed job 수가 일치하고 모든 채택 job을 재생 가능.

## 5. Memory Overhead

**주장:** 기본 파라미터에서 데이터 규모와 KV size에 따른 메모리 비용을 정량화한다.

- 5.3의 P0, KV 100/10240 B, size 2000/4000/8000 GiB 총 6개 설정의 실행을 공유한다.
- 100 ms 간격 process RSS와 OS peak RSS를 기록하고, phase 전환마다 descriptor allocation을 집계한다.
  sampling overhead와 짧은 peak 누락 가능성은 pilot에서 확인한다.
- 구성: PLR segments/capacity, KMV global/range sketches, 기타 vSST metadata,
  generation/sort buffers, materialization buffers, memtable/cache, 기타 프로세스 메모리.
- 실제 allocation을 측정하고 단순 struct 크기를 전체 RSS로 대체하지 않는다.
  공유 allocation은 중복 계산하지 않으며 freed/live/allocator reserved의 차이를 명시한다.
- component peak의 합과 total peak는 다르다. 같은 시점의 구성만 stacked bar로 그린다.
- 표현: dataset size별 peak RSS 곡선, KV별 peak RSS 점, peak 시점의 구성 막대/표.
  baseline RSS도 같은 logger로 보조 기록한다. 기본 error bound/sketch size는 고정한다.
- 완료: OS peak와 샘플 peak를 함께 보존하고, descriptor와 process memory 간 차이를 설명한다.

## 6. Parameter Sensitivity

**주장:** approximation parameter의 정확도·실행 시간·메모리 tradeoff와 기본값 선택 근거를 보인다.

| 축 | 제안 값 | 나머지 조건 |
|---|---|---|
| PLR error bound | 2/4/8/16/32 | KMV samples 512 고정 |
| KMV samples | 128/256/512/1024/2048 | PLR error bound 8 고정 |

- 기본 조합 공유 시 9개의 고유 조합. range buckets=8 등 나머지는 고정한다.
  코드에서는 `--plr_error_bound`, `VCOMP_KMV_SAMPLES`, `VCOMP_KMV_RANGE_BUCKETS` 경로가 확인되었다.
  invalid env가 default로 fallback하므로 effective 값을 로그로 검증한다.
- 먼저 5.4 capture에서 모든 조합을 replay한다. 이미 작은 sketch로 만든 descriptor를
  단순 확대하지 말고 동일 exact input keys로 각 budget의 descriptor를 새로 구축한다.
- 이어 P0에서 9개 F2Load 조합의 end-to-end 결과를 확인한다. 기본값은 호환되면 Fidelity DB를 공유한다.
  원 baseline은 공통이므로 조합마다 재로딩하지 않는다.
- 모든 조합에서 peak memory, model build/virtual compaction/loading time,
  cardinality/split/size error, final DB/per-level size, YCSB C filter checks/positive lookups를 수집한다.
- 5.4 replay timing은 동일 job 10회 반복하고, end-to-end는 조합별 우선 1회로 한계를 표시한다.
- 표현: x=parameter, y=각 실제 비용/오차의 small multiples. 기본값을 표시한다.
  메모리 절은 기본 설정의 규모 확장, 이 절은 설정 변경의 영향으로 역할을 구분한다.
- 완료: 각 값을 실제 사용했음을 검증하고, 성능이 좋다는 이유로 fidelity 실패 조합을 숨기지 않는다.

## 7. Portability

**주장:** 접근법을 다른 LSM-tree based KV store에 구현해 자체 baseline에 대한 이점을 확인한다.

- 1개 추가 store를 대상으로 한다. LevelDB를 우선 후보로 두되, target repository/version과
  정상 leveled compaction/SST materialization interface를 확인한 뒤 target manifest를 동결한다.
  아직 포팅되었거나 CLI가 존재한다고 가정하지 않는다.
- 구현 분리: PLR/KMV/virtual merge-split 재사용 범위와 picker/Version/SST writer adaptation을 구분한다.
  변경 파일/코드 규모, 기능 제한을 기록한다. LOC 하나로 이식성을 결론 내리지 않는다.
- 순서: build/reopen fixture → 1–4 GiB pilot → 100 GiB qualification → 1000 GiB full comparison.
- 100/1000 GiB에서 target baseline/ported loader를 각각 측정한다.
  1000 GiB는 양쪽 3회 독립 로딩을 제안하고 각 source의 fresh copy로 YCSB C/A를 실행한다.
  100 GiB는 qualification 결과임을 구분하며 3회 통계에 포함하지 않는다.
- target 안에서 지원되는 공통 설정을 맞춘다. RocksDB의 48 background jobs 등 지원하지 않는
  옵션을 억지로 적용하지 않고 target별 effective 설정을 공개한다.
- 지표: loading time, total writes, 구조, throughput/mean latency, positive lookups,
  compaction write bytes; filter counter는 target에서 같은 정의로 계측한 경우만 비교한다.
- 표현: integration 범위 표 + 규모별 loading plot + 대표 fidelity actual-value 표/점.
- filesystem aging/device placement의 동일성이나 임의의 모든 LSM 구조 지원을 주장하지 않는다.
- 완료: baseline과 port의 자체 검증, 원 DB 재열기, full run provenance, 제한 사항을 함께 제시한다.

## 8. 재사용 근거와 필요한 runner 변경

| 기존 자료/도구 | 재사용 범위 | 먼저 확인하거나 수정할 부분 |
|---|---|---|
| `PAPER_EVALUATION_PLAN.md`, `results/README.md` | 과거 load/DB inventory | fixed-record KV sweep과 coupled memtable/SST는 새 sweep과 다름 |
| `PAPER_F2LOAD_REPEAT_F11_F15_260912.md` 및 repeat bundles | 독립 source DB와 timed A–F | 서로 다른 binary, 300초/무 drain 결과를 새 full protocol로 합치지 않음 |
| `PAPER_FILLSEQ_SPARSE_260913.md`, fixed-A runner | 3장 A의 옵션·count·drain 검증 | generic fixed-work runner로 확장, 정확한 clone 방식 재검증 |
| `scripts/eval/run_ycsb_matrix.py` | 옵션/수집 코드 참고 | hardlink staging, 자동 rmtree, duration 중심 실행을 그대로 사용하지 않음 |
| `scripts/read/run_ycsb_alternatives.py` | 검증된 계측 옵션 참고 | frozen 300초 전용 경로를 변경해 역사적 결과를 재정의하지 않음 |
| `FLUSH_PROFILER_INSTRUMENTATION.md` | FlushJob 경계 및 lazy sort 계측 | instrumented elapsed와 CPU 구분, profiler overhead 확인 |
| `REAL_INPUT_COMPACTION_ACCURACY.md` | capture/replay 및 KMV budget | capture 제외 timer, bounded sampling, current build qualification |
| `tools/db_bench_tool.cc` phase/register counters | phase1/bg_wait/phase2와 registration 상세 | cumulative/overlap semantics 및 누락 시간 audit |

구현할 도구는 신규 `scripts/eval/ch5/` runner/collector와 `analysis/ch5/` parser/plotter로
분리하는 것을 제안한다. 현재 이 경로의 실행 도구를 작성했다는 의미는 아니다.
필수 기능은 manifest dry-run, immutable binary snapshot, physical-copy 검증, fixed-operation
YCSB, 완료 대기, source identity audit, 계측 구간별 counter reset, 재시작 시 중복 방지이다.
기존 mutable binary를 실험 도중 rebuild하지 않는다.

## 9. 산출물과 실행 순서

### 산출물

새 run마다 `artifacts/ch5/<campaign>/<experiment>/<config>/<method>/<rep>/`에 raw evidence를,
검증 후 `results/paper_ch5_<campaign>/`에 다음 파일을 둔다.

- `manifest.json`: build/patch/binary hash, exact argv/env allowlist, hardware, source/copy paths,
  all effective options, seeds, fixed-work budgets, completion/copy/warm-up protocol.
- `loading.tsv`: run/config/method/rep, input records/bytes, time, total writes, CPU, RSS, measured/projected.
- `structure.tsv`: run/source/level, SST count/bytes, range/candidate summaries.
- `workloads.tsv`: source/copy/workload, request counts, foreground time, latency sums/counts,
  filter counters, found count, compaction bytes, drain duration/status.
- `io.tsv`: run/stage/device, read/write counts/bytes와 측정 시작/끝.
- `flush_registration.tsv`, `compaction_pairs.tsv`, `phases.tsv`, `memory.tsv`, `sensitivity.tsv`.
- `validation.json`, `reuse_inventory.tsv`, `RESULTS.md`, plotting source, PDF와 PNG.

모든 row는 원시 파일과 run ID로 추적 가능해야 한다. 단위는 column에 명시하고 missing을 0으로 채우지 않는다.
수치 확정 전 manuscript에 결과를 자동 반영하지 않는다.

### 실행 순서와 gate

1. **Inventory:** 재사용 후보의 source/build/options/units/완료/복사/측정 protocol을 audit한다.
   `reuse_inventory.tsv`에 reuse / remeasure / incompatible / missing을 이유와 함께 기록한다.
2. **계측 및 runner 준비:** timer 경계, deep copy, fixed-A/F별 budget, memory counters,
   capture sampling과 삭제 없는 resume를 구현한다. source DB 변경 없이 dry-run manifest를 검증한다.
3. **작은 pilot:** 1–4 GiB 기능 fixture, 16 GiB loader/YCSB C/A/E qualification,
   100 GiB representative workload/capture qualification 순서로 확장한다.
   stage별 failure와 counter 누락을 의도적으로 주입해 validator가 거부하는지 확인한다.
4. **대표 P0 한 쌍:** end-to-end loading, 구조, full C/A, operation timing, RSS/phase를 확인한다.
   fidelity 불일치는 임의 허용값으로 숨기지 않고 분석한다. full matrix 확대 전 구현 버전을 동결한다.
5. **Fidelity 10+10:** DB 단위 load → 구조 → A–F, 방법 교대. 호환되는 기존 DB는 재사용한다.
6. **Flexibility:** 작은 설정부터 큰 설정으로 진행하며 phase/RSS를 공유 수집한다.
   sensitivity는 capture replay부터 시작하고 full source 공유 가능성을 확인한다.
7. **Portability:** target adapter 개발과 작은 pilot 후 별도 target campaign을 실행한다.
8. **검증/그림:** 실제 값/점 중심으로 작성하고 1–3장 수치·범위·용어와 cross-check한다.

### 중단/완료 조건과 자원 산정

- 실행 실패, reopen/iterator 검증 실패, source identity 변경, copy mode 불일치,
  잘못된 옵션, incomplete capture, 기대 count 불일치, 누락 timer/counter는 해당 cell 실패다.
- 방법 간 구조/성능 차이가 크다는 사실은 측정 실패가 아니라 과학적 결과다.
  유효한 부정적 결과를 버리거나 baseline에 가까운 run만 채택하지 않는다.
- swap/OOM, free-space 부족, competing I/O는 실행을 멈추고 원인/부분 evidence를 보존한다.
- 저장량은 모든 보존 source DB + 보존 workload copies + growth/drain writes + capture bytes로 계산한다.
  source 20개만 세어 120개 fresh-copy 비용을 빠뜨리지 않는다. 공간이 모자라면 실행 범위를 나누고
  저자의 보존 지시를 바꾸지 않는다. 자동 삭제가 필요한 runner를 그대로 실행하지 않는다.
- 소요 시간은 pilot의 load + copy + workload + drain + verify를 cell별 합산한다.
  과거 300초 workload만 곱한 시간은 전체 캠페인 ETA가 아니다. 현재 검증된 전체 ETA는 없다.
- 새 subsection별 결과는 table/figure와 provenance, failed/retried counts, limitation까지 갖춰야 완료다.

### 과거 계획과 달라진 결정

- KV axis는 record 고정이 아니라 logical input 고정이다.
- target SST만 바꾸며 memtable은 고정한다.
- Fidelity는 3회가 아니라 각 방법 10회 독립 로딩이다.
- normalized 그래프 대신 원수치와 개별 점을 기본으로 한다.
- fresh physical copies 및 fixed-work/drain 조건을 명시한다.
- 기존 source DB와 결과를 보존하며 자동 삭제 계획을 계승하지 않는다.
- L2/L3/L4는 loading 비용 분석으로 두며 ablation으로 분리하지 않는다.
