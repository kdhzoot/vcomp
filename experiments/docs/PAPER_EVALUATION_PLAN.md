# F2Load evaluation plan

> 2026-09-13: 새 Chapter 5 실험 설계는 [PAPER_CHAPTER5_EXPERIMENT_PLAN.md](PAPER_CHAPTER5_EXPERIMENT_PLAN.md)를 따른다. 아래는 과거 캠페인 기록이며, 새 계획과 다른 sweep 조건·normalized 표현·DB 삭제 절차를 그대로 적용하지 않는다.

## 공통 조건

- 비교 대상: conventional loading (baseline) vs F2Load, 동일 설정
- 고정 옵션과 변인 목록은 README의
  [Canonical experiment configuration](../../README.md#canonical-experiment-configuration).
  LSM 형태를 결정하는 옵션은 RocksDB 기본값에 맡기지 않고 전부 고정한다
- 기준 point (**P0**): KV 24 B + 1000 B, logical input 1000 GiB = **1,048,576,000 records**,
  memtable·target SST 64 MiB, L1 base 256 MiB, multiplier 10, kMinOverlappingRatio.
  각 실험은 자기 축만 변화시키고 나머지는 이 값
- 입력: baseline은 `fillrandom`, F2Load는 합성 생성기, 둘 다 63.2% unique.
  동일 키 시퀀스가 필요한 membership 비교는 트레이스 경로를 따로 쓴다
- 로딩 지표: 로딩 시간, WAF, total disk write
- 워크로드: YCSB A~F, MixGraph
- block cache: 데이터셋 크기의 0%, 5%, 10%
- fidelity 지표 (baseline 대비 normalized): throughput, latency, 발행한 I/O 횟수,
  SST 접근 횟수, compaction bytes
- baseline은 P0에서 3회 반복해 런 간 변동을 구하고, F2Load의 오차는 그 변동과 비교해
  판정한다. seed는 12345678로 고정하므로 이 변동은 **BG 스케줄링 변동만** 포함한다.
  입력 시퀀스 변동까지 넣으려면 seed를 바꾼 반복이 따로 필요하다
- cache 3단계는 양쪽 테스트베드가 같은 결정(설정 간 순위·상대 격차)을 내는지 확인하는
  축으로도 사용한다

## E1. KV size

record 수 1,048,576,000 고정, KV size **100 B / 500 B / 1 KB / 5 KB / 10 KB**
(→ input 98 / 488 / 1000 / 5000 / 10000 GiB)

record 수를 고정하므로 KV가 커지면 데이터셋도 함께 커진다. 5 KB는 5 TB로 L5까지,
10 KB는 10 TB로 E2-8T보다 큰 트리를 만든다. KV 크기 효과와 트리 깊이 효과가 섞인다는
점을 해석에 반영해야 한다.

## E2. Dataset size

KV size 1 KB 고정, logical input **1 / 2 / 4 / 8 TB**

## E3. Key uniqueness

unique ratio **25% / 50% / 75% / 100%**

`fillrandom`은 키 도메인이 `num`과 같아 unique 비율이 63.2%로 고정된다. 이 축은 트레이스
경로(`baseload` + VLOADTR1 `--unique-ratio`)로만 만들 수 있고, 네 point 모두 그 경로를 쓴다.

## E4. Level 설정

L1 base **64 MiB / 256 MiB / 1 GiB** × multiplier **4 / 10**

데이터 크기를 고정한 채 레벨 수만 바꾸는 축이다. virtual compaction을 몇 세대 통과하느냐에
따라 근사 오차가 누적되는지를 규모 변화와 분리해서 측정한다.
ㅊ
## E5. Memtable / SST size

memtable과 target SST size를 함께 **16 MiB / 64 MiB / 256 MiB**

## E6. Compaction policy

compaction priority **kMinOverlappingRatio / kOldestSmallestSeqFirst /
kByCompensatedSize / kOldestLargestSeqFirst**

## E7. Compression

compression_type **none / lz4 / zstd**

F2Load은 출력 크기를 count x 설정된 KV 크기로 예측한다. 압축을 켜면 실제 SST 바이트가
데이터 엔트로피에 좌우되므로 split 경계와 레벨 점유가 어긋날 수 있다.

## E8. SST metadata

**bloom_bits 0 / 16**, **partition_index_and_filters=true**, **use_ribbon_filter=true**
(기준은 bloom_bits 10, 둘 다 false)

필터·인덱스가 SST 바이트를 바꾸므로 E7과 같은 크기 예측 위험을 공유하되, 원인이 압축이
아니라 메타데이터라는 점이 다르다. db_bench에는 `index_type` 플래그가 없어 인덱스 구조는
`partition_index_and_filters`로 바꾼다.

## E9. Virtual compaction accuracy

baseline 로딩의 각 compaction job에 대해 그 입력 SST로 vSST를 만들어 virtual compaction을
실행하고 실제 출력과 비교한다. 별도 로딩이 필요 없고 baseline 로딩에 캡처를 얹으면 된다.
→ output SST 개수 오차, output 크기 오차 CDF, KMV dedup 카디널리티 오차

## 필요한 DB와 로딩 현황

기준 2026-09-10 21:01 KST. 각 축의 중심점은 P0와 같은 설정이라 DB를 공유하지만, 축마다 어떤 point가
필요한지 보이도록 **중복을 합치지 않고 전부 나열한다**. `DB` 열이 실제로 로딩되는
디렉터리이고, 각 point는 baseline과 F2Load 두 DB가 필요하다. 이 표는
`experiments/scripts/eval/status_table.py`로 다시 생성한다.

| 실험 | point | 변경 | DB | baseline | F2Load |
|---|---|---|---|---|---|
| P0 반복 (noise floor) | P0-r1 | 기준 | `P0-r1` | 완료 **0.95 h** | 완료 **0.02 h** |
|  | P0-r2 | 기준 | `P0-r2` | 완료 **0.93 h** | 완료 **0.02 h** |
|  | P0-r3 | 기준 | `P0-r3` | 완료 **0.93 h** | 완료 **0.02 h** |
| E1 KV size | E1-100B | KV 100 B | `E1-100B` | 완료 **0.28 h** | 대기 ~0.02 h |
|  | E1-500B | KV 500 B | `E1-500B` | 완료 **0.59 h** | 대기 ~0.02 h |
|  | E1-1KB | KV 1 KB (= P0) | `P0-r1` | 완료 **0.95 h** | 완료 **0.02 h** |
|  | E1-5KB | KV 5 KB | `E1-5KB` | 완료 **4.55 h** | 대기 ~0.04 h |
|  | E1-10KB | KV 10 KB | `E1-10KB` | 대기 ~11.64 h | 대기 ~0.10 h |
| E2 Dataset size | E2-1T | input 1 TB (= P0) | `P0-r1` | 완료 **0.95 h** | 완료 **0.02 h** |
|  | E2-2T | input 2 TB | `E2-2T` | 완료 **2.01 h** | 대기 ~0.07 h |
|  | E2-4T | input 4 TB | `E2-4T` | 완료 **4.51 h** | 대기 ~0.21 h |
|  | E2-8T | input 8 TB | `E2-8T` | 완료 **10.39 h** | 완료 **0.72 h** |
| E3 Uniqueness | E3-u25 | unique 25% (트레이스 경로) | `E3-u25` | 대기 ~0.53 h | 대기 ~0.02 h |
|  | E3-u50 | unique 50% (트레이스 경로) | `E3-u50` | 대기 ~0.80 h | 대기 ~0.02 h |
|  | E3-u75 | unique 75% (트레이스 경로) | `E3-u75` | 대기 ~1.06 h | 대기 ~0.02 h |
|  | E3-u100 | unique 100% (트레이스 경로) | `E3-u100` | 대기 ~1.33 h | 대기 ~0.02 h |
| E4 Level 설정 | E4-64m4 | L1 64 MiB, mult 4 | `E4-64m4` | 완료 **0.98 h** | 대기 ~0.02 h |
|  | E4-64m10 | L1 64 MiB, mult 10 | `E4-64m10` | 완료 **0.98 h** | 대기 ~0.02 h |
|  | E4-256m4 | L1 256 MiB, mult 4 | `E4-256m4` | 완료 **0.97 h** | 대기 ~0.02 h |
|  | E4-256m10 | L1 256 MiB, mult 10 (= P0) | `P0-r1` | 완료 **0.95 h** | 완료 **0.02 h** |
|  | E4-1024m4 | L1 1 GiB, mult 4 | `E4-1024m4` | 완료 **1.05 h** | 대기 ~0.02 h |
|  | E4-1024m10 | L1 1 GiB, mult 10 | `E4-1024m10` | 완료 **1.11 h** | 대기 ~0.02 h |
| E5 Memtable/SST | E5-16 | 16 MiB | `E5-16` | 완료 **1.33 h** | 대기 ~0.02 h |
|  | E5-64 | 64 MiB (= P0) | `P0-r1` | 완료 **0.95 h** | 완료 **0.02 h** |
|  | E5-256 | 256 MiB | `E5-256` | 완료 **1.09 h** | 대기 ~0.02 h |
| E6 Compaction policy | E6-MinOverlappingRatio | compaction_pri 3 (= P0) | `P0-r1` | 완료 **0.95 h** | 완료 **0.02 h** |
|  | E6-ByCompensatedSize | compaction_pri 0 | `E6-ByCompensatedSize` | 완료 **1.13 h** | 대기 ~0.02 h |
|  | E6-OldestLargestSeq | compaction_pri 1 | `E6-OldestLargestSeq` | 완료 **1.10 h** | 대기 ~0.02 h |
|  | E6-OldestSmallestSeq | compaction_pri 2 | `E6-OldestSmallestSeq` | 완료 **1.06 h** | 대기 ~0.02 h |
| E7 Compression | E7-none | none (= P0) | `P0-r1` | 완료 **0.95 h** | 완료 **0.02 h** |
|  | E7-lz4 | lz4 | `E7-lz4` | 완료 **1.28 h** | 대기 ~0.02 h |
|  | E7-zstd | zstd | `E7-zstd` | 완료 **4.61 h** | 대기 ~0.02 h |
| E8 SST metadata | E8-bloom10 | bloom_bits 10 (기본) (= P0) | `P0-r1` | 완료 **0.95 h** | 완료 **0.02 h** |
|  | E8-bloom0 | bloom_bits 0 | `E8-bloom0` | 완료 **1.02 h** | 대기 ~0.02 h |
|  | E8-bloom16 | bloom_bits 16 | `E8-bloom16` | 로딩 중 | 대기 ~0.02 h |
|  | E8-partitioned | partition_index_and_filters | `E8-partitioned` | 대기 ~0.94 h | 대기 ~0.02 h |
|  | E8-ribbon | use_ribbon_filter | `E8-ribbon` | 대기 ~1.03 h | 대기 ~0.02 h |

행 37개 = 축별로 세어 중복 포함. 실제 DB는 60개이고 그중 26개 완료.
남은 로딩 baseline 17.3 h + F2Load 0.9 h.
E9 virtual compaction accuracy는 별도 DB 없이 baseline 로딩에 캡처를 얹는다.

예측값은 night1 실측 두 점(P0 0.936 h, 8 TB 10.394 h)에 맞춘
`t = 0.729·TB^1.208 + 0.197n·10⁻⁹` 모델이고, E3~E8은 여기에 축의 효과를 추정해 얹었다.
대형 point(2 TB 이상, KV 5 KB)에서는 −2~−12%로 잘 맞고, **1 TB급에서 +20~60% 낮게** 나온다.
F2Load은 records가 고정된 축에서 메타데이터 작업이 하한이라 1~2분 아래로 내려가지 않는다.

DB 크기는 logical input의 0.68~0.82배로 흔들린다(정착 시점 차이).

## 실행

러너 `experiments/scripts/eval/run_eval_campaign.py` — LSM 형태를 결정하는 db_bench 옵션을
전부 고정한다(README의 Canonical experiment configuration). `load.sh`는
`level_compaction_dynamic_level_bytes`, `format_version`을 명시하지 않아 쓰지 않는다.
DB root는 캠페인 전체가 `/work/vcomp/exp/eval_20260909_night1`을 공유하므로 이미 로딩된
point는 자동으로 건너뛴다. 큐는 `--queue-file`로 준다.

| 캠페인 | 내용 | 상태 |
|---|---|---|
| `eval_20260909_night1` | E2-8T (F2Load+baseline), P0 ×3 (baseline+F2Load) | 완료 8/8 |
| `eval_20260909_batch2` | E1-100B, E1-500B, E4 5점, E5 2점 (baseline) | 완료 9/9 |
| `eval_20260909_ordered` | 번호 순서 baseline 12점 | 진행 중 5/12 |

`eval_20260909_batch3`(싼 것부터 배치)은 E1 우선순위에 맞춰 번호 순서 큐로 교체하면서
폐기했다. 교체 과정에서 E7-lz4가 25분 진행된 상태로 중단되어 부분 DB를 삭제했고, 그
point는 번호 순서 큐의 E7 자리에 다시 넣었다.

로딩 완료 22건 (합 35.7 h):

| point | mode | 로딩 | DB | 예측 대비 |
|---|---|---:|---:|---:|
| E1-100B | baseline | 0.28 h | 0.08 TB | +10% |
| E1-500B | baseline | 0.59 h | 0.37 TB | +18% |
| E1-5KB | baseline | 4.55 h | 3.71 TB | -12% |
| E2-2T | baseline | 2.01 h | 1.55 TB | -2% |
| E2-4T | baseline | 4.51 h | 2.94 TB | -2% |
| E2-8T | baseline | 10.39 h | 6.66 TB | 기준 |
| E2-8T | F2Load | 0.72 h | 5.80 TB | — |
| E4-1024m10 | baseline | 1.11 h | 0.76 TB | +32% |
| E4-1024m4 | baseline | 1.05 h | 0.85 TB | +63% |
| E4-256m4 | baseline | 0.97 h | 0.74 TB | +38% |
| E4-64m10 | baseline | 0.98 h | 0.73 TB | -5% |
| E4-64m4 | baseline | 0.98 h | 0.70 TB | +26% |
| E5-16 | baseline | 1.33 h | 0.83 TB | +23% |
| E5-256 | baseline | 1.09 h | 0.82 TB | +22% |
| E6-ByCompensatedSize | baseline | 1.13 h | 0.77 TB | +21% |
| E6-OldestLargestSeq | baseline | 1.10 h | 0.82 TB | 기준 |
| P0-r1 | baseline | 0.95 h | 0.82 TB | 기준 |
| P0-r1 | F2Load | 0.02 h | 0.82 TB | — |
| P0-r2 | baseline | 0.93 h | 0.82 TB | 기준 |
| P0-r2 | F2Load | 0.02 h | 0.81 TB | — |
| P0-r3 | baseline | 0.93 h | 0.82 TB | 기준 |
| P0-r3 | F2Load | 0.02 h | 0.82 TB | — |

실측에서 드러난 것:

- **1 TB speedup 49.3x** (P0 baseline 0.936 h / F2Load 0.019 h), **8 TB speedup 14.4x**
- **baseline 런 간 변동 2.7%** (0.926~0.951 h). F2Load 오차는 이 폭과 비교해 판정한다
- **E2 축**: 1→8 TB에서 로딩이 11.0배로 초선형(WAF 증가). DB/input 비율이 0.68~0.77로
  ±6% 흔들리므로, 8 TB에서 관측된 baseline 6.66 vs F2Load 5.80 TB(−13%) 격차가 이
  변동폭을 넘는지가 판정 지점이다
- **E4는 로딩 시간이 아니라 DB 크기를 바꾼다.** 0.70~0.85 TB로 21% 벌어지는데 로딩 시간은
  0.97~1.11 h로 거의 같다. multiplier 4가 빨라질 것이라는 예측은 틀렸다
- **E5는 단조가 아니다.** 16 MiB 1.33 h, 64 MiB(P0) 0.95 h, 256 MiB 1.09 h로 64 MiB 근처가
  가장 빠르다
- **E1-5KB는 L5까지 6개 레벨을 만든다.** record 수 고정 축이라 데이터가 5 TB로 커져
  P0(L4)보다 깊다. KV 크기 효과와 트리 깊이 효과가 섞인다는 점을 해석에 반영해야 한다

디스크: 2026-09-09에 6월 성능 튜닝 DB 11개(16 TB)와 `/work/db/2500gb_full_filter`(2.7 TB)를
삭제해 여유를 확보했다. 삭제한 튜닝 런의 수치는 README에 남아 있다. 9월 fidelity 캠페인 중
run2/run3/run4와 real_input의 DB도 앞서 삭제했고, artifacts와 git 번들은 보존했다.

## 남은 일

1. **ordered 큐 잔여 7점** — E6-OldestSmallestSeq, E7 2점, E8 4점. 약 7 h
2. **E3 4점** — `baseload` + VLOADTR1 트레이스가 필요해 아직 큐에 없다. 트레이스 4개
   (1 TB, unique 25/50/75/100%)를 만들면 이어붙일 수 있다. 로딩 합 3.7 h
3. **F2Load 전량** — baseline이 끝난 뒤 일괄 진행한다. P0 ×3과 E2-8T만 되어 있고
   나머지 21개 DB가 남았다. 합 0.8 h
4. **읽기 매트릭스** — 실행 스크립트가 아직 없다. point당 full 3.5 h / 축소 0.5 h이고,
   읽기가 끝난 point는 삭제해 공간을 회수한다

## 2026-09-14 결정
- F2Load 로딩은 backpressure(`--vcomp_l0_backpressure`, 기본 on) + `vcomp_exact_membership=true` 바이너리(cb43cda202 이후)로 전부 재로딩됨(`/work/vcomp/exp/fix_20260913/`). 수정 전 F2Load DB는 모두 삭제.
- **E5-256 baseline은 r2**(`fix_20260913/E5-256-r2_baseline`)를 사용. r1은 L1 파일 1개가 도메인 78%를 덮어 block-cache 핸들 경합으로 처리량이 38% 낮게 측정된 run — 폐기(DB 삭제, 결과는 artifacts에만 보존).
- **E1-5KB, E1-500B 축은 사용하지 않음** (DB 삭제). E1은 100B / 1KB(P0) / 10KB.
- 진단용 DB(dbg*), 9/8 파일럿 DB, /work/background, /work/mbftest 삭제.
- positive lookup 지표 = (memtable+L0+L1+L2+ hit) / number.keys.read. 이전 표의 found% 열은 무효(Get 수 기반 추정치).
- (09-14 오후) **Target SST size 축 제외.** 최종 설정 13개: P0 + KV 100 B/10 KB + dataset 2/4/8 TB + uniqueness 25/50/75/100% + LZ4 + level (256M,×4)/(1G,×10).
  E5-sst16/E5-sst256 DB(baseline·F2Load)는 삭제하지 않고 보관.
