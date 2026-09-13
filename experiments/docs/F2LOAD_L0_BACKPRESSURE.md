# F2Load 깊은 레벨 coverage 붕괴 — 원인과 수정 (2026-09-13)

## 증상
baseline 대비 F2Load의 per-level key-range coverage가 L3/L4에서 무너진다.
1 TB E4-64m4: L4 81.5→37.8, L5 85.1→55.7. P0: L3 87.5→71.8, L4 97.0→86.4.
filter check per lookup이 baseline 2.11–5.11 범위인데 F2Load는 2.42–3.68로 압축되는 직접 원인.
100 GB 이하에서는 거의 재현되지 않고(E4 100 GB: L4 63.0 vs 72.8), 1 TB에서 뚜렷하다.

## 기각된 가설
- intra-L0 compaction 비활성화(b53e2a4a45): 되살려도 coverage 65.23→65.26. vcomp에선 L0가 안 쌓여
  62건만 발생(baseline 378건). 원인 아님. (`VCOMP_INTRA_L0=1` 게이트로 남아 있음)
- picker sweep 순서: baseline과 동일(평균 |Δ위치|/도메인 0.225 vs 0.196, 전진비율 0.645 vs 0.634).
- materialization: 가상 상태와 실체화 후 key range 완전 일치. 무죄.

## 원인
`DBImpl::RefillVirtualL0WindowLocked`는 **가시 L0 바이트만** 보고 다음 배치를 넣는다. 창 크기 기본값은
`max_compaction_bytes` = 25 × `target_file_size_base` = 1600 MiB로 레벨 타깃과 무관하게 고정.
Phase 1이 L0 파일을 version에 직접 등록해 write path를 우회하므로 baseline의 write stall
(`level0_slowdown/stop_writes_trigger`, `soft/hard_pending_compaction_bytes_limit`)이 전혀 작동하지 않는다.

`level_compaction_dynamic_level_bytes=false`에서 L0 score = max(파일수/trigger, L0바이트/L1타깃)
(version_set.cc:4018). 1600 MiB 창이면 E4는 25, P0는 6.25. 피드가 공짜라 compaction 풀이 항상 포화이고,
포화된 score 스케줄러는 모든 레벨을 L0 score 근처에서 평형시킨다. 실측 L1 팽창 23.8× / 6.2×.
backlog는 상한이 없어 데이터셋이 클수록(E2 축), 레벨 타깃이 작을수록(E4/E5 축) 커진다.
마지막 `waitforcompaction`이 이 backlog를 한꺼번에 몰아 내리면서 key 공간에 덩어리진 레이아웃이 남는다.

### 로딩 중 레벨별 중앙크기/타깃 (진행 20% 이후, MANIFEST 재생)
| 1 TB E4-64m4 | L0 GiB | L1 | L2 | L3 | L4 | L5 | compaction 입력 | 로딩 |
|---|---|---|---|---|---|---|---|---|
| baseline | 0.81 | 2.00 | 0.96 | 1.06 | 1.02 | 1.03 | 19.41 TiB | 3,581 s |
| F2Load (수정 전) | 1.59 | 23.6 | 8.6 | 7.5 | 7.3 | 6.7 | 14.08 TiB (−27%) | 65 s |
| mode 1: slowdown 조건에서 정지 | 0.00 | 0.00 | 1.00 | 1.04 | 1.07 | 1.05 | 18.41 TiB | 55 s |
| mode 2: 1 + 어떤 레벨 score≥1이면 정지 | 0.00 | 0.00 | 0.99 | 0.99 | 1.02 | 1.02 | 18.40 TiB | 73 s |
| **mode 3: hard 정지 + soft 감속** | **0.25** | **3.05** | **1.15** | **1.17** | **1.07** | **1.04** | **18.75 TiB (−3.4%)** | **62 s** |
| mode 4: L0 trigger만 | 1.52 | 23.0 | 7.7 | 6.6 | 6.1 | 5.8 | 14.25 TiB | 62 s |

같은 E4 설정도 100 GB에서는 L2–L5가 0.86–1.04×라 재현이 안 됐던 것.

## 수정 (`VCOMP_L0_BACKPRESSURE`, db_impl_compaction_flush.cc `pressure` 람다)
mode 3 = baseline의 stall 의미론 그대로: `level0_stop_writes_trigger` 또는 `hard_pending_compaction_bytes_limit`
이면 피드 **정지**, `level0_slowdown_writes_trigger` 또는 `soft_pending_compaction_bytes_limit`이면 **한 파일씩만 admit**(감속).
mode 4가 실패했으므로 실제로 일하는 조건은 pending bytes. 설정 스윕 어느 축에서도 별도 튜닝 없음.

### 최종 coverage
| 1 TB E4-64m4 | L2 | L3 | L4 | L5 | L4 중앙갭 |
|---|---|---|---|---|---|
| baseline | 100 | 80.9 | 80.6 | 88.3 | 433 |
| F2Load 수정 전 | 100 | 11.1 | 33.3 | 59.7 | 5,764,478 |
| mode 3 (2회) | 100 | 53.4 / 56.4 | 78.4 / 75.8 | 83.7 / 83.4 | 318 |

| 1 TB P0 | L3 | L4 | L4 파일수 | L5 |
|---|---|---|---|---|
| baseline r1/r2/r3 | 87.6 / 85.6 / 86.8 | 97.0 / 97.0 / 96.5 | 4171 / 4120 / 4142 | 99.98 |
| F2Load 수정 전 (캠페인 / 재실행) | 71.8 / 76.1 | 86.4 / 91.5 | 3643 / 3764 | 99.95 |
| **mode 3** | **85.3** | **94.7** | **4065** | 99.98 |

P0는 L3가 baseline 밴드(85.6–87.6) 경계, L4는 밴드 −1.8 pt, L4 파일 수도 복원.
E4는 L4/L5가 2–5 pt 이내로 들어왔고 L3(파일 18–21개, ~1 GiB)만 −25 pt 잔존 — F2Load 자체 run-to-run 변동(±1.5–3 pt)보다 크므로 실제 잔여 격차.
baseline의 L1/L2 coverage는 run 간 변동이 크다(L1 17–80, L2 54–73) — 지표로 쓰기 부적합.

로딩 시간(fillvirtual): E4 65.1→62.2 s (2회 61.6 s, 오히려 −4%), P0 60.7→64.5 s (+6%). 가속 E4 55×→58×. materialization 17.6–21.3 s 불변.

## 부수 관찰
- 최하단 레벨 파일 크기: baseline은 60–70%가 64 MiB를 살짝 넘김(임계 도달 후 자름), F2Load는 정확히 64 MiB에서 자름(초과 ~1%). 파일 수 ~2% 차이. 별건.
- baseline은 재오픈 시 MANIFEST가 스냅샷으로 압축돼 history가 사라진다. 트레이스가 필요하면 첫 오픈 뒤 즉시 복사할 것. F2Load DB는 원본 MANIFEST-000005가 남는다.

## 도구
- `experiments/scripts/eval/manifest_dump.cc` — MANIFEST 덤프 (`g++-11 $(PLATFORM_CXXFLAGS) -I. -Iinclude manifest_dump.cc librocksdb.a $(PLATFORM_LDFLAGS)`)
- `experiments/scripts/eval/replay_manifest.py` — compaction 단위 복원(배치 커밋을 key-range 연결요소로 분리), 흐름/폭 통계
- `experiments/scripts/eval/gap_analysis.py` — `VCOMP_COV_PERFILE=1 db_bench --benchmarks=coverage` 출력으로 갭 구조
- `experiments/scripts/eval/sweep_order.py` — compaction 순서 비교

## DB 위치
`/work/vcomp/exp/dbg1t_20260913/` — t1k-e4_baseline(MANIFEST 사본 `MANIFEST-000005`), t1k-e4_f2load_{off,on,on2,on3,on3b,on4}, t1k-p0_f2load_{off,on3}.
`/work/vcomp/exp/dbg100_20260913/`, `dbg_20260913/`, `dbg_intral0/` — 100 GB / 10 GB 재현.

## 정식 구현 후 검증 (2026-09-13 18:03, origin 847e198c08 위에 구현)
`--vcomp_l0_backpressure`(기본 on) = mode 3. 러너 F2 옵션에 `vcomp_exact_membership=True` 추가(847e198c08의 bitmap key set).
P0 설정 F2Load 1 TB 2벌 → YCSB C zipfian 5분, cache 50 GiB (캠페인 설정 동일):

| | ops/s | p50/p99 µs | filter/read | found % | SST read/op | SST 수 |
|---|---|---|---|---|---|---|
| baseline P0-r1 | 1,474,872 | 73.1 / 166.6 | 3.427 | 98.48 | 0.222 | 13,539 |
| F2Load 수정 전 | 929,071 (−37%) | 73.2 / 109.5 | 3.682 | 98.49 | 0.223 | 12,470 |
| F2Load 수정 후 r1 | 1,580,368 (+7.2%) | 69.5 / 109.7 | 3.209 | 98.55 | 0.221 | 13,261 |
| F2Load 수정 후 r2 | 1,532,047 (+3.9%) | 68.9 / 109.7 | 3.758 | 98.54 | 0.222 | 13,210 |

로딩 56.2 / 57.0 s (Phase 1 18.5 s — exact membership이 단일 스레드 key stream, BG 15.6 s, materialization 21.4 s).
backpressure 통계: stops=0, trickles≈1,200–1,360 — hard 정지는 한 번도 없고 soft 감속만 걸림.
baseline YCSB C는 r1 한 벌뿐이라 +4~7%와 filter/read 편차(3.21/3.76)가 baseline 변동 안인지는 P0-r2/r3 YCSB C가 있어야 판정 가능.
