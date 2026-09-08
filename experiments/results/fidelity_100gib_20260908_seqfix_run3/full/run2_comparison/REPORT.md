# 100GiB seq 수정 재실험: run2와 run3 비교

Sequence/iterator 정합성만 수정한 binary로 1GiB pilot 후 100GiB full matrix를 1회 실행했다. 각 단계에서 baseline 6개와 F2 6개를 동시에 시작했고 12/12 검증을 완료했다. Full baseline 6개는 trace의 unique 수와 일치하며, 수정 F2 6개는 clean RocksDB iterator의 strict increasing 검사를 통과했다.

D는 생성 전 live descriptor 엔트리 합, M은 materialization 직후 live SST 엔트리 합, U는 최종 visible distinct key 수다. run2의 U는 독립 bitmap 감사 결과를 사용한다. 순서 검사가 실패한 run2의 기존 `exact_unique_keys`는 iterator 행 수이므로 U로 사용하지 않았다.

| 조건 | D 기존 → 수정 | M 기존 → 수정 | U 기존 → 수정 | U 상대오차 기존 → 수정 |
|---|---:|---:|---:|---:|
| s100gib_1024B_unique100 | 102,141,825 → 101,999,402 | 101,932,665 → 101,744,338 | 84,492,727 → 84,487,007 | -19.421% → -19.427% |
| s100gib_1024B_uniform50 | 61,757,251 → 61,780,317 | 61,731,283 → 61,752,023 | 53,618,263 → 53,609,965 | +2.269% → +2.253% |
| s100gib_1024B_zipf99_50 | 55,488,488 → 55,499,455 | 55,456,726 → 55,471,965 | 50,346,326 → 50,141,610 | -3.972% → -4.362% |
| s100gib_91B_unique100 | 1,147,801,456 → 1,146,031,749 | 1,145,523,877 → 1,143,330,268 | 950,917,744 → 949,616,388 | -19.409% → -19.520% |
| s100gib_91B_uniform50 | 689,305,907 → 689,632,856 | 688,676,325 → 689,068,118 | 587,240,490 → 587,580,583 | -0.462% → -0.405% |
| s100gib_91B_zipf99_50 | 618,517,232 → 618,297,925 | 617,946,708 → 617,820,112 | 558,271,709 → 558,329,556 | -5.373% → -5.363% |

| 조건 | 기존 중복 iterator 행 | 수정 non-increasing 행 | 수정 pending bytes | trace SHA-256 일치 |
|---|---:|---:|---:|---|
| s100gib_1024B_unique100 | 17,236,895 | 0 | 0 | True |
| s100gib_1024B_uniform50 | 8,099,867 | 0 | 0 | True |
| s100gib_1024B_zipf99_50 | 5,110,400 | 0 | 0 | True |
| s100gib_91B_unique100 | 184,447,374 | 0 | 0 | True |
| s100gib_91B_uniform50 | 101,435,835 | 0 | 0 | True |
| s100gib_91B_zipf99_50 | 59,226,363 | 0 | 0 | True |

같은 입력 trace를 사용했어도 동시 실행 스케줄에 따라 가상 compaction 결과 D/M/U는 달라질 수 있다. 따라서 두 대규모 캠페인의 차이를 sequence 수정 단독의 정확도 효과로 해석하지 않는다. Sequence 교정 자체는 앞선 동일 생성 배치의 128MiB old/new 파일럿에서 별도로 확인했다. 이번 실행은 수정 경로가 여섯 100GiB 조건에서 정상 iterator를 제공하는지와 남아 있는 cardinality 오차를 측정한다.

Baseline 적재는 F2 적재와 동일한 캠페인별 frozen `f2_db_bench`에서 `baseload`와 `use_virtual_compaction=false`를 사용한다. 후속 settling은 고정 clean RocksDB executable, 최종 scan은 clean RocksDB 정적 라이브러리에 연결한 checker를 사용한다.

이 baseline/settle/reader 구성은 이전 runner 그대로다. 이번 측정 도중 실행 binary를 변경하지 않았으며, 완료 시 frozen executable SHA-256을 manifest와 다시 대조했다. Descriptor/materialization 정확도 수정은 이번 실행에 포함하지 않았다.

입력 trace 6개 SHA-256의 run2/run3 일치 여부와 원본 JSON 해시는 `comparison.json`에 보존했다. 기존 run2 DB/trace/결과를 변경하지 않았고, 재실험 DB와 trace는 별도 run3 `/work` 경로에 생성했다. 명령·환경·실행 상태·소스/바이너리 해시는 run3 manifest, case별 로그 및 `seqfix_source_hashes.json`에서 확인할 수 있다. 시간은 동시 실행의 운영 기록이며 성능 비교 수치로 사용하지 않는다.
