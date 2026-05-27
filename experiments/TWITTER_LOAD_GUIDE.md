# Twitter Trace Load Quick Guide

Twitter trace로 baseline/vcomp loading을 재현하는 최소 절차.

## 1. 준비

`vcomp/`는 binary와 converter, `eval-vcomp/`는 실행 wrapper와 log 관리 담당.

```bash
cd /home/smrc/virtual_compaction/vcomp
DEBUG_LEVEL=0 LIB_MODE=static CXX=g++-11 CC=gcc-11 make -j"$(nproc)" db_bench
```

필요 파일:

```text
vcomp/db_bench
vcomp/tools/twitter_trace_convert.py
eval-vcomp/load_twitter.sh
```

## 2. Trace 변환

원본 Twitter trace는 `cache-trace/README.md`의 링크에서 받는다.
압축된 `.zst`를 `.vcomptrace`로 변환:

```bash
cd /home/smrc/virtual_compaction
zstd -dc twitter_traces/cluster12.sort.zst \
  | python3 vcomp/tools/twitter_trace_convert.py - \
      twitter_traces/cluster012_10M.vcomptrace \
      --max-ops 10000000 \
      --progress
```

현재 load 실험은 기본적으로 put-only 변환을 사용한다.

## 3. 실행

먼저 10M으로 smoke test 권장.

Baseline:

```bash
cd /home/smrc/virtual_compaction
RUN_NAME=baseline-twitter-10m \
MODE=baseline \
TRACE_FILE=/home/smrc/virtual_compaction/twitter_traces/cluster012_10M.vcomptrace \
MAX_OPS=10000000 \
DB_ROOT=/work/vcomp \
bash eval-vcomp/load_twitter.sh
```

VComp:

```bash
cd /home/smrc/virtual_compaction
RUN_NAME=vcomp-twitter-10m \
MODE=vcomp \
TRACE_FILE=/home/smrc/virtual_compaction/twitter_traces/cluster012_10M.vcomptrace \
MAX_OPS=10000000 \
DB_ROOT=/work/vcomp \
EXTRA_DB_BENCH_ARGS='--vcomp_trace_shard_direct_io=true' \
bash eval-vcomp/load_twitter.sh
```

250GB급 실험은 기존 trace에서 240M put 사용:

```bash
TRACE_FILE=/home/smrc/virtual_compaction/twitter_traces/cluster012_500M.vcomptrace
MAX_OPS=240000000
```

나머지 명령은 위와 동일하게 `RUN_NAME`, `MODE`만 바꿔 실행.

## 4. 결과 위치

```text
eval-vcomp/log_loads/<RUN_NAME>_<timestamp>/bench.out
eval-vcomp/log_loads/<RUN_NAME>_<timestamp>/raw/load_cmd.sh
eval-vcomp/log_loads/<RUN_NAME>_<timestamp>/raw/primary_benchmark_sec.txt
/work/vcomp/<RUN_NAME>/
```

시간 확인:

```bash
LOG=eval-vcomp/log_loads/vcomp-twitter-10m_YYMMDD_HHMM
cat "$LOG/raw/primary_benchmark_sec.txt"
grep -E '^(twitterload|fillvirtual)[[:space:]]*:' "$LOG/bench.out"
```

## 5. 주의사항

- `MODE=baseline`: `twitterload` 실행
- `MODE=vcomp`: `fillvirtual --twitter_trace_file` 실행
- `DB already exists`가 나오면 `RUN_NAME`을 새로 지정
- trace value size가 크면 `RG_VALUE_SIZE=4194304`처럼 늘려서 실행
