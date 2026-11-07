# F2Load: grandparent 경계에서의 출력 파일 분할 정렬 (2026-11-07)

## 요약

> **결론 (11-07):** 원인은 두 가지였다. (1) grandparent 경계에서 자르는 위치가 PLR 근사로 몇 키 빗나감(§3~5), (2) materialize된 SST의 `oldest_ancester_time = 0` 때문에 RocksDB의 TTL 기준 출력 분할이 모든 F2Load 파일 경계에서 작동(§8). 둘 다 고친 빌드에서 YCSB A의 compaction 동작과 처리량이 baseline과 일치한다(§8.1).

- **증상:** F2Load로 만든 1 KB 2 TB·4 TB DB에서 YCSB A(읽기 50 / 쓰기 50) 처리량이 같은 크기 baseline보다 9~12% 낮았다. YCSB C(읽기만)는 차이가 없었다.
- **직접 원인:** YCSB A 중 compaction 하나가 끌어들이는 아래 레벨 파일 수가 baseline의 약 1.7배(L3: 4.0 → 7.1개)이고, 출력 SST가 절반 크기(60 → 27 MB)로 잘린다. compaction 한 번이 30~50% 오래 걸려 pending compaction bytes stall이 3~4배 늘었다.
- **근본 원인:** virtual compaction은 RocksDB처럼 grandparent 파일 경계에서 출력을 자르지만, 자르는 위치를 `round(PLR.Predict(경계 키))`로 정한다. PLR 오차(error bound 16)만큼 위치가 어긋나고, 그 위치에서 `Inverse()`로 되살린 파일 키 범위가 경계를 몇 키 넘어선다. 그래서 인접 두 출력 파일이 같은 grandparent 파일에 모두 겹친다. 1 TB DB에서 F2Load는 L4→L5 파일 경계의 67%가 아래 파일 하나를 가르고(baseline 6.6%), 가르는 경우의 95%는 아래 파일 키 범위의 0.1% 미만(몇 키)만 침범한다. 경계를 고르는 방식이 아니라 근사 오차의 문제라는 뜻이다.
- **수정:** 자르는 위치를 `Predict`의 반올림이 아니라 "`Inverse(p) >= 경계 키`가 처음 성립하는 p"로 찾는다. 키 집합은 그대로이고 파일을 끊는 위치만 바뀐다. 경계 키의 의미를 "자른 뒤 오른쪽 파일의 첫 키"로 통일해서, grandparent 파일의 smallest와 largest + 1을 경계로 넘긴다.
- **검증:** 단위 테스트 `VirtualSSTTest.GrandparentCutsDoNotStraddleBoundaries`(수정 전 실패, 수정 후 통과). 1 TB F2Load 재로딩 후 경계 정렬 비율과 YCSB C·A를 baseline과 비교 (결과는 아래 "실험" 절에 추가).

## 1. 관측: 2 TB·4 TB YCSB A

E 세트(설정별 fidelity) 2 TB·4 TB 행. 같은 크기의 baseline DB와 F2Load DB에서 YCSB C·A를 각 5분, 32 threads, cache는 데이터셋의 5%로 측정 (`run_e_scale_queue.sh`, 결과 `experiments/artifacts/e_scale_ycsb_jh/`).

| 지표 (baseline → F2Load) | 2 TB | 4 TB |
|---|---|---|
| YCSB C ops/s | 1.159 M → 1.118 M (−3.5%) | 1.078 M → 1.117 M (+3.7%) |
| YCSB A ops/s | 646 K → 588 K (−9.1%) | 612 K → 536 K (−12.3%) |
| 쓰기 평균 지연 | 29.5 → 39.7 µs | 29.0 → 45.5 µs |
| 쓰기 p99 | 444 → 614 µs | 444 → 614 µs |
| `rocksdb.db.write.stall` 횟수 | 13,408 → 21,774 | 6,687 → 21,451 |
| LOG의 "Stalling writes because of estimated pending compaction bytes" | 45 → 166 | 122 → 353 |
| 쓰기 1개당 compaction write bytes | 8.12 → 8.20 KB (+1%) | 9.54 → 9.94 KB (+4%) |
| 읽기(get) 평균 지연 | 67.9 → 67.6 µs | 73.9 → 72.3 µs |
| `sst.read.micros` P50 / P99 (A) | 80.3/389 → 78.5/400 µs | 80.7/414 → 79.7/443 µs |

- compaction 총량(write bytes)은 F2Load가 더 적다(723 대 788 GB, 800 대 875 GB). 처리량이 낮아 쓴 키 수가 적기 때문이고, 쓰기 1개당으로는 1~4% 차이다. 즉 "compaction을 더 많이 해서" 느린 것이 아니다.
- SST 읽기 지연과 get 지연은 같다. 읽기 경로 문제가 아니다.
- 차이는 전부 쓰기 stall이다. stall 사유는 거의 pending compaction bytes 임계 초과.

## 2. compaction 단위 분석 (YCSB A 중 EVENT_LOG)

`compaction_started`/`compaction_finished` 이벤트를 짝지어 출력 레벨별로 집계 (스크립트는 scratchpad의 `cstat.py`; LOG는 `e_scale_ycsb_jh/<db>__workloada/LOG`).

2 TB (괄호는 4 TB), baseline → F2Load:

| 출력 레벨 | 아래 레벨 입력 파일 수 / job | 출력 파일 평균 크기 | 소요 시간 / job |
|---|---|---|---|
| L2 | 1.43 → 2.20 (1.60 → 2.61) | 55 → 39 MB (56 → 38) | 0.46 → 0.58 s (0.44 → 0.51) |
| L3 | 4.01 → 7.06 (4.21 → 7.78) | 60 → 27 MB (60 → 26) | 0.95 → 1.45 s (0.86 → 1.24) |
| L4 | 4.55 → 4.24 (4.81 → 4.58) | 63 → 36 MB (61 → 31) | 1.02 → 1.34 s (0.97 → 1.23) |
| L1, L5 | 차이 없음 | 61~62 MB | 차이 없음 |

- 위 레벨에서 내려오는 파일은 레벨마다 1개(1.00~1.15)로 같다. 그 1개 파일이 겹치는 아래 레벨 파일 수가 F2Load에서 늘어난다.
- 출력 파일이 작아지는 것은 양쪽 경계에서 각각 잘리기 때문이다. 작은 파일은 다음 compaction에서 다시 더 많은 파일과 겹친다(연쇄).
- 로딩 직후의 레벨별 파일 수·크기는 baseline과 거의 같다 (4 TB: L5 42,911 / 42,587개, 2,515 / 2,520 GB). 1 TB SST 크기 분포도 같다(평균 61.1 대 63.6 MB). 즉 파일 "크기"가 아니라 파일 "경계 위치"의 문제다.

## 3. 경계 정렬 측정 (1 TB DB)

보관 중인 1 TB DB 두 개를 읽기 전용으로 열어 live SST 메타데이터(레벨, smallest/largest 키, 크기)를 덤프하고(`GetLiveFilesMetaData`, scratchpad `lsmmeta.cc`, RocksDB 10.10.1 정적 라이브러리로 빌드), 인접 레벨 사이 파일 경계를 비교했다 (`align.py`, `miss.py`).

- baseline: `/work/vcomp/exp/baseline_1010_jh_261104/baseline_1kb` (13,489 SST)
- F2Load: `/work/vcomp/exp/f2load_w20_r3/f2load_1kb` (12,958 SST, HEAD 1eabb0145 + L0 윈도우 20, KMV 512/8, PLR 16)

**L 레벨의 인접 파일 사이 경계가 L+1 파일 하나를 가르는 비율** (위 파일 i의 largest와 i+1의 smallest 사이에 아래 파일 하나가 걸쳐 있음):

| 레벨 쌍 | baseline | F2Load |
|---|---|---|
| L2→L3 | 4 / 47 (8.5%) | 12 / 42 (28.6%) |
| L3→L4 | 29 / 450 (6.4%) | 194 / 427 (45.4%) |
| L4→L5 | 273 / 4,125 (6.6%) | 2,677 / 3,982 (67.2%) |

**가르는 경우 얼마나 침범했나** (잘린 아래 파일에서 작은 쪽 조각이 키 범위에서 차지하는 비율):

| 레벨 쌍 | baseline p50 / p90 | F2Load p50 / p90 | F2Load 0.1% 미만 |
|---|---|---|---|
| L3→L4 | 0.018% / 0.127% | 0.003% / 0.011% | 192 / 194 |
| L4→L5 | 1.24% / 29.6% | 0.000% / 0.002% | 2,550 / 2,677 |

baseline에서 가르는 경우는 size-based cut(경계와 무관하게 크기로 잘림)이라 침범 폭이 크고 드물다. F2Load에서 가르는 경우는 거의 전부 몇 키 수준의 침범이다. 경계에서 자르려 했으나 몇 키 빗나간 것이다. RocksDB는 경계 침범이 몇 키여도 겹치는 아래 파일 전체를 compaction 입력에 넣으므로, 이 작은 침범이 §2의 fan-in 증가로 그대로 이어진다.

## 4. 코드 분석

### 4.1 RocksDB의 실제 동작 (`db/compaction/compaction_outputs.cc`)

- `UpdateGrandparentBoundaryInfo(key)`: 현재 키가 grandparent 파일의 largest를 넘어가면(key > largest) 경계 1회, 다음 grandparent 파일의 smallest에 도달하면(key >= smallest) 경계 1회를 센다.
- `ShouldStopBefore(key)`: 경계를 하나 이상 넘은 키 앞에서, 현재 출력 파일 크기가 `target × (50 + 5 × 넘은 경계 수)%`(최대 90%) 이상이면 자른다. 자르는 위치는 정확히 "경계를 넘은 첫 키" 앞이므로 출력 파일이 grandparent 경계를 넘는 일이 없다.

### 4.2 virtual compaction의 동작 (수정 전)

- `db/db_impl/db_impl_compaction_flush.cc` (가상 compaction 본체, ~6008행): grandparent 파일마다 smallest, largest 키(uint64 prefix)를 `gp_boundaries`에 넣고 정렬·dedup하여 `SplitIntoSSTs`에 넘긴다.
- `db/virtual_compaction/virtual_sst.cc` `SplitIntoSSTs`:
  1. 경계 키 → 위치: `gp_positions.push_back(round(plr.Predict(bkey)))`. **여기서 PLR 오차(error bound, 실험 16)만큼 어긋난다.**
  2. 위치 열을 훑으며 RocksDB와 같은 dynamic threshold로 자를 위치(`split_positions`)를 고른다. 이 부분은 RocksDB를 잘 따른다.
  3. 파일 i의 키 범위를 `key_start = plr.Inverse(pos_start)`, `key_end = plr.Inverse(pos_end − 1)`로 정한다.
- `tools/db_bench_tool.cc` materialize(~8234행): 각 vSST의 sub-PLR을 위치 0, 1, 2, …로 걸어 `Inverse`와 같은 공식으로 키를 만들고 `[materialize_key_min, materialize_key_max]`(= vSST key_min/max, 같은 레벨 이웃과 겹치면 min을 올림)로 clamp한다. 따라서 **파일의 실제 키는 `Inverse` 기준**이다.
- 결과: `Predict(B)`가 참 위치보다 d만큼 작으면 왼콍 파일이 B 이상 키를 d개 가지고(아래 파일 침범), 크면 오른쪽 파일이 B 미만 키를 d개 가진다. 어느 쪽이든 인접 두 출력이 같은 grandparent 파일에 겹친다.
- 경계 키 의미도 불일치: largest 키 자체를 경계로 넘기므로, 정확히 잘라도 largest 키가 오른콍 파일로 간다(RocksDB는 왼쪽에 남긴다).

### 4.3 성립 조건 확인

- `PLRModel::Inverse(pos)`(`plr_model.cc`): 세그먼트별 선형식의 역함수. 세그먼트의 위치 구간이 단조 증가하므로 pos에 대해 단조 비감소. 구간 사이 빈틈은 가장 가까운 세그먼트로 clamp.
- materialize의 키 생성 공식은 `Inverse`와 같고(세그먼트 선형 걷기 + 세그먼트 범위 clamp), 추가로 vSST key_min/max clamp와 "직전 키 + 1" 보정만 있다. 따라서 `key_end < B <= key_start(다음)`이면 materialize된 키도 그 관계를 지킨다.
- `Inverse`는 세그먼트 수 S에 선형(O(S))이지만, 로딩 로그 기준 가상 compaction 입력의 세그먼트 수는 평균 460~6,200개이고 경계당 `Inverse` 호출은 10여 회라 job당 1 ms 미만이다 (기존 split 단계가 job당 40~4,600 µs).

## 5. 수정 내용

### 5.1 `virtual_sst.cc`: `GrandparentCutPosition`

```
p = round(Predict(B))            // 기존 추정 (fallback)
reaches(p) := Inverse(p) >= B
Inverse(lo) < B <= Inverse(hi)가 되도록 p 주변을 1, 2, 4, … 단계로 넓혀 bracket을 잡고
이분 탐색으로 hi − lo == 1까지 좁힘 → 자를 위치 = hi
```

- 결과 위치 hi는 `Inverse(hi − 1) < B <= Inverse(hi)`. 왼쪽 파일 `key_end = Inverse(hi − 1) < B`, 오른쪽 파일 `key_start = Inverse(hi) >= B`.
- 모델이 경계 근처에서 단조가 아니거나(bracket 실패, 2^16 위치 이상 넓혀야 함) 위치 0에 닿으면 기존 추정값을 그대로 쓴다(이전 동작).
- `Predict`가 범위 밖(0 이하, total 이상)이면 경계를 버린다(이전과 같음).

### 5.2 경계 키의 의미 통일

- `db_impl_compaction_flush.cc`, `compaction_job.cc`(accuracy probe 경로): grandparent 파일마다 `smallest`와 `largest + 1`을 넣는다. "자른 뒤 오른쪽 파일의 첫 키"라는 한 가지 의미가 된다. `virtual_sst.h`의 주석에 명시.

### 5.3 바뀌지 않는 것

- 키 집합: materialize되는 키는 `Inverse(0), Inverse(1), …`로 전과 같다. 파일을 몇 번째 키 다음에 끊는지만 바뀌므로 키 간격, 분포, entry 수에 영향이 없다. 자르는 위치는 PLR 오차 범위(±16 위치, 64 MB 파일에서 약 ±16 KB) 안에서만 움직인다.
- size-based cut, dynamic threshold 로직, L0 출력 미분할, KMV 추정, PLR merge.

### 5.4 단위 테스트

`db/virtual_compaction/virtual_sst_test.cc` `GrandparentCutsDoNotStraddleBoundaries`: 불규칙 간격 키 6,000개에 error bound 8 PLR을 맞추고, 250키 단위 grandparent 파일 경계(smallest, largest + 1)를 넘겨 분할한다. 250키마다 한 번씩 잘리는지, 인접한 두 출력 파일이 같은 grandparent 파일에 함께 겹치지 않는지(키 범위와 materialize된 키 둘 다) 확인한다.

- 처음 쓴 단언 "어느 파일도 경계 양쪽 키를 갖지 않는다"는 틀렸다. 자르지 않기로 한 경계(threshold 미달)는 RocksDB에서도 파일 안에 들어가며, largest + 1과 다음 smallest 사이의 빈 구간에서 시작하는 파일도 정상이다. 실제로 막아야 하는 것은 "인접 두 출력이 같은 grandparent 파일에 겹치는 것"이다.
- 수정 전 코드: 실패 (`outputs [25417, 49614] and [49720, 74133] both overlap grandparent [25375, 49750]`). 수정 후: 전체 13개 테스트 통과 (`old_test.out`, `new_test.out`).
- 테스트 라이브러리는 `DEBUG_LEVEL=0`으로 빌드되지 않아(`db_test_util.cc`가 SyncPoint를 요구) `make virtual_sst_test` 대신 테스트 소스 + `test_util/testharness.cc` + fused gtest를 release `librocksdb.a`에 직접 링크해서 돌렸다. 수정 전 비교는 `git show HEAD:virtual_sst.cc`를 따로 컴파일한 object를 앞에 링크해서 만들었다.

### 5.5 빌드 사고 (기록)

첫 db_bench 빌드(sha 1e83aff6…)는 **수정 전 `SplitIntoSSTs`가 들어간 반쪽 빌드**였다. 수정 전/후 테스트를 돌리려고 `virtual_sst.cc`를 잠시 HEAD 버전으로 바꿨다가 `cp -p`로 되돌렸는데, 보존된 mtime이 그 사이 컴파일된 `virtual_sst.o`보다 오래되어 make가 재컴파일하지 않았다. 호출자(`db_impl_compaction_flush.cc`, largest + 1)만 새 코드였다. 이 빌드로 로딩한 1 TB DB는 L4→L5 경계의 35%가 아래 파일을 갈랐다(수정 전 67%). 경계 키 의미만 바꿔도 절반은 줄지만 근사 오차는 그대로라는 뜻이다. 결과는 `*_halffix*`로 이름을 바꿔 두었고 DB는 지웠다. `touch` 후 재빌드한 바이너리(sha deb7d04c…)로 다시 로딩했다.

## 6. 실험 (수정 후 비교)

- 빌드: `make DEBUG_LEVEL=0 db_bench` (HEAD 03cea5540 + 이 수정, dirty). 수정 전 바이너리는 `db_bench.03cea5540`으로 보관.
- 로딩: `RUN_ID=f2load_jh_1k_1tb_align`, 1 TB · 1 KB, KMV 512/8, PLR 16, 32 threads (`run_f2load_1kb.sh`). 동시에 1 KB 8 TB baseline 로딩이 돌고 있었으므로 로딩 시간은 참고치.
- 측정: 경계 정렬 비율(§3과 같은 방법), coverage, YCSB C·A (5분, 32 threads, cache 50 GiB) — baseline_1010_jh_261104와 비교. run-id `align_ycsb_jh`.
- 로딩 결과: phase1 128.3 s + phase2 6.9 s = 135.2 s (8 TB baseline과 동시 실행; 단독 124 s), peak RSS 474 MiB, DB 827.1 GB (baseline 823.8, 수정 전 823.7~824.7), SST 13,628개 (baseline 13,489, 수정 전 12,958~13,057). SST 수가 baseline에 가까워진 것은 경계에서 정확히 잘라 파일이 더 늘어났기 때문이다.
- **경계 정렬 (수정 전 → 수정 후, 괄호는 baseline):**

| 레벨 쌍 | 아래 파일을 가르는 경계 비율 | 위 파일 1개가 겹치는 아래 파일 수 (fan-out) |
|---|---|---|
| L2→L3 | 28.6% → 9.5% (8.5%) | 6.95 → 7.70 (6.73) |
| L3→L4 | 45.4% → 2.5% (6.4%) | 8.81 → 8.59 (8.12) |
| L4→L5 | 67.2% → 4.3% (6.6%) | 2.68 → 2.13 (2.11) |

  남은 경계 침범(L4→L5 183건)은 침범 폭 중앙값 5.5%, p90 37.6%로 baseline의 size-based cut과 같은 모양이다. 몇 키만 빗나간 침범(0.1% 미만)은 2,550건에서 73건으로 줄었다.
- **YCSB C·A** (둘 다 8 TB baseline 로딩과 동시 실행, 처리량은 참고치; run-id `align_ycsb_jh`):

| 지표 (baseline 1 TB → 수정 후 F2Load 1 TB) | 값 | 수정 전 (2 TB) |
|---|---|---|
| YCSB C ops/s | 950 K → 947 K (−0.3%) | −3.5% |
| YCSB A ops/s | 516 K → 488 K (−5.4%) | −9.1% |
| `db.write.stall` 횟수 | 16,813 → 26,769 (1.6×) | 1.6× |
| pending compaction bytes stall 로그 | 30 → 68 | 45 → 166 |
| 쓰기 1개당 compaction write | 7.29 → 7.37 KB (+1%) | +1% |
| L3 compaction의 아래 레벨 입력 파일 수 | 3.80 → 5.66 (+49%) | 4.01 → 7.06 (+76%) |
| L3 compaction 시간 | 1.16 → 1.39 s (+20%) | 0.95 → 1.45 s (+53%) |
| L2 / L3 / L4 출력 SST 평균 크기 | 55/60/68 → 36/31/37 MB | 55/60/63 → 39/27/36 MB |

  경계 정렬은 baseline 수준이 됐지만 **출력 SST가 여전히 절반 크기**이고 그만큼 fan-in과 stall이 남는다. 출력 파일 크기 분포(`jobdetail.py`): L3 출력 4,598개 중 **16 MB 미만이 2,147개** (baseline 2,802개 중 88개). 경계 어긋남으로는 설명이 안 되는, 다른 원인의 "잘게 자르기"다. → §8.

## 8. 두 번째 원인: F2Load SST의 oldest_ancester_time = 0 (TTL 기준 출력 분할)

§6의 "16 MB 미만 출력 파일 2,147개"의 원인. 경계 정렬과는 별개의 버그다.

- **RocksDB 동작** (`compaction_outputs.cc` `FillFilesToCutForTtl`, `UpdateFilesToCutForTTLStates`): level compaction + `compaction_pri = kMinOverlappingRatio`(실험 설정 3) + `ttl > 0`이면, 출력 레벨 입력 파일 중 `oldest_ancester_time < now − ttl/2`이고 크기가 target/2보다 큰 파일을 "곧 만료될 파일"로 보고, **그 파일들의 경계마다 출력을 자른다**(다음 TTL compaction을 싸게 하려는 최적화). 실험의 `Options.ttl`은 315,360,000 s(10년, db_bench 기본값)라 now − 5년이 기준이다.
- **F2Load 파일의 시각**: `tools/db_bench_tool.cc` materialize install(~8537행)과 `db_impl_compaction_flush.cc:5386`의 `AddFile`이 `oldest_ancester_time = 0, file_creation_time = 0`을 넣는다. `TryGetOldestAncesterTime()`은 0이면 table property `creation_time`으로 대체하는데 SstFileWriter가 쓴 파일은 그것도 0이다. 확인: 1 TB F2Load DB(`f2load_w20_r3`)의 12,958개 파일 전부 `oldest_ancester_time == 0`, baseline 13,489개는 전부 로딩 시각(1793771412~1793775309) (`lsmtime.cc`).
- **결과**: 0 < now − 5년이므로 F2Load가 만든 모든 파일(≥32 MB)이 "만료 직전" 파일로 분류되고, YCSB A 중 실제 compaction은 아래 레벨 입력 파일 하나하나의 경계에서 출력을 끊는다. 파일 범위 사이·바깥에 걸친 조각이 16 MB 미만 파일로 쏟아지고, 다음 compaction의 fan-in이 다시 늘어난다. baseline 파일은 하루 전 시각이라 해당 없음.
- **영향 범위**: F2Load DB에서 돌린 모든 쓰기 워크로드(YCSB A, 2·4 TB 포함, 로딩 대안 비교의 F2Load 쪽은 해당 없음). 읽기 전용 YCSB C는 영향 없음. TTL compaction 자체는 `oldest_ancester_time == 0`을 "알 수 없음"으로 건너뛰므로(`ComputeExpiredTtlFiles`) 트리거되지 않았다(compaction_reason은 전부 LevelMaxLevelSize).
- **수정안**: materialize install과 fillvirtual L0 등록에서 `oldest_ancester_time`과 `file_creation_time`을 현재 시각(초)으로 넣는다. baseline의 flush가 하는 것과 같다(flush는 memtable의 가장 오래된 키 시각, 없으면 현재 시각). F2Load 로딩은 몇 분이라 로딩 시작 시각과 현재 시각의 차이는 무시할 수 있다.

### 8.1 적용과 결과 (2026-11-07 11:46 KST)

- 수정: `tools/db_bench_tool.cc` materialize install과 `db_impl_compaction_flush.cc` fillvirtual L0 등록의 `AddFile`에 `oldest_ancester_time = file_creation_time = 설치 시각(초)`. 가상 compaction 출력(가상 파일, materialize 때 삭제됨)은 그대로 0.
- 빌드 sha deefcf28…(경계 수정 + TTL 수정). 경계 수정만 들어간 빌드는 `db_bench.alignfix`로 보관.
- 로딩 `f2load_jh_1k_1tb_ttl`: phase1 126.3 s + phase2 7.2 s (8 TB baseline과 동시), DB 826.8 GB, SST 13,672. 경계 정렬 L2→L3 9.1%, L3→L4 1.4%, L4→L5 5.0% (baseline 8.5 / 6.4 / 6.6%).
- YCSB (baseline은 §6과 같은 run, 모두 8 TB 로딩과 동시):

| 지표 | baseline | 경계 수정만 | 경계 + TTL 수정 |
|---|---|---|---|
| YCSB C ops/s | 950 K | 947 K | 949 K |
| YCSB A ops/s | 516 K | 488 K (−5.4%) | 516 K (0.0%) |
| `db.write.stall` 횟수 | 16,813 | 26,769 | 14,784 |
| pending compaction bytes stall 로그 | 30 | 68 | 28 |
| 쓰기 1개당 compaction write | 7.29 KB | 7.37 KB | 7.27 KB |
| L3 compaction 아래 입력 파일 수 | 3.80 | 5.66 | 3.88 |
| L3 compaction 시간 | 1.16 s | 1.39 s | 1.23 s |
| L3 출력 중 16 MB 미만 파일 | 88 / 2,802 | 2,147 / 4,598 | 98 / 2,787 |
| L2·L3·L4 출력 평균 크기 | 55·60·68 MB | 36·31·37 MB | 55·60·67 MB |

  두 수정을 합치면 YCSB A 중 compaction의 논리 동작(레벨별 job 수, fan-in, 출력 크기 분포, 쓰기당 compaction 양)이 baseline과 일치한다. 경합 없는 처리량은 큐의 E 1 TB 확인(`run_e_1tb_check.sh`)에서 다시 잰다.

## 7. 영향 범위와 후속

- 이 수정 이후 빌드되는 `db_bench`를 쓰는 큐: PLR 민감도(`run_plr_sweep_reps_1kb.sh`), E 1 TB 확인(`run_e_1tb_check.sh`). 앞선 KMV 민감도(G), C의 F2Load 5회, 2·4 TB F2Load는 수정 전 빌드 결과다.
- YCSB C 결과는 영향이 없어야 한다(읽기 경로 동일). YCSB A와 Fig 13(설정별 fidelity)의 쓰기 섹션은 수정 후 빌드로 다시 재는 것이 맞다.
- 측정 도구: scratchpad `lsmmeta.cc`(read-only open) + `align.py`, `miss.py`, `cstat.py`. 재사용하려면 `experiments/analysis/`로 옮길 것.
