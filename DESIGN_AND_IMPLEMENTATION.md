# Design and Implementation

이 파일은 논문 본문의 **Design and Implementation** section으로 바로 옮길 수
있도록, 독자가 F2Load의 디자인을 순서대로 이해하는 흐름에 맞춰 재구성한
초안이다. 문장은 최종 논문에서 더 압축해도 되고, 여기서는 각 subsection이
무엇을 설명해야 하는지와 현재 구현의 핵심 내용을 분명히 남긴다.

## 1. Design Overview

이 subsection의 목적은 F2Load가 무엇을 fast-forward하고, 무엇을 보존하는지
먼저 정의하는 것이다.

### 핵심 메시지

- F2Load는 LSM-tree dataset loading에서 가장 비싼 부분인 repeated compaction
  I/O를 fast-forward한다.
- 단순히 SSTable file을 임의로 만들어 붙이는 것이 아니라, RocksDB의 metadata
  evolution과 compaction decision path를 이용해 final LSM tree structure를
  만든다.
- Loading 중에는 key-value record 대신 virtual SSTable descriptor를 이동시키고,
  benchmark 실행 전 마지막 단계에서 실제 SSTable로 materialize한다.

### 논문에 들어갈 구조

1. Conventional loading의 병목:
   - Insert -> memtable -> flush -> repeated compaction -> final SSTs
   - Compaction은 input SST read + merge + output SST write를 반복한다.

2. F2Load의 대체 경로:
   - Generate key sequence
   - Build virtual L0 SSTables
   - Run virtual compactions over descriptors
   - Materialize final descriptors into real SSTables

3. Preserved vs. fast-forwarded components:

| Category | F2Load behavior |
|----------|-----------------|
| Key distribution | PLR model로 vSST 내부 key sequence 표현 |
| File metadata | RocksDB VersionSet에 실제 file metadata로 등록 |
| Compaction selection | RocksDB CompactionPicker 사용 |
| Manifest / Version update | RocksDB LogAndApply path 사용 |
| Intermediate compaction I/O | 제거 |
| Final SSTable format | RocksDB SstFileWriter로 실제 SST 생성 |

### Figure로 넣기 좋은 내용

```
Input sequence
   -> Virtual flush
   -> Pending L0 window
   -> Visible vSSTs in RocksDB VersionSet
   -> Virtual compaction
   -> Final vSST tree
   -> Materialization
   -> Real RocksDB SSTables
```

## 2. Virtual SSTable Abstraction

이 subsection은 F2Load가 왜 개별 key-value pair 없이도 loading 중 compaction을
진행할 수 있는지 설명한다.

### 핵심 메시지

- vSST는 physical SSTable의 in-memory descriptor이다.
- vSST는 file-level metadata와 key distribution model만 저장한다.
- RocksDB metadata path에서는 vSST가 일반 SSTable처럼 보이지만, data block은
  아직 존재하지 않는다.

### vSST가 저장하는 정보

| Field | Purpose |
|-------|---------|
| `file_number` | RocksDB VersionSet에서 file identity로 사용 |
| `level` | LSM tree level |
| `key_min`, `key_max` | File key range |
| `num_entries` | Logical key-value pair count |
| `size_bytes` | Level size, compaction score 계산에 사용 |
| `plr_model` | Key -> local rank mapping |
| `kmv_sketch` | Cross-SST dedup/cardinality 추정을 위한 작은 key fingerprint sample |
| `kmv_ranges` | Key-range별 KMV sketch; local dedup/cardinality 추정에 사용 |

### 논문에서 강조할 점

- `size_bytes`는 실제 file size가 아니라, RocksDB의 level size accounting과
  compaction trigger를 realistic하게 작동시키기 위한 logical size이다.
- `key_min/key_max`와 `level`은 compaction picker가 overlap과 level structure를
  판단하는 데 필요하다.
- `plr_model`은 나중에 materialization할 때 key sequence를 복원하고, virtual
  compaction에서 merged key distribution shape를 계산하는 데 사용된다.
- `kmv_sketch`는 PLR이 보존하지 못하는 key identity 정보를 작은 sample로
  보존하여, cross-SST duplicate cardinality를 추정하는 데 사용된다.

## 3. Modeling a Sorted Run with PLR

이 subsection은 vSST 내부의 key sequence를 어떻게 compact하게 표현하는지
설명한다.

### 핵심 메시지

- 하나의 sorted SSTable은 key에서 rank로 가는 monotonic function으로 볼 수
  있다.
- F2Load는 이 function을 piecewise linear regression으로 근사한다.
- PLR model은 compaction 중 merge 가능한 learned descriptor이자, 마지막
  materialization에서 inverse로 key를 복원하는 descriptor이다.

### 설명 순서

1. Sorted key sequence `K = {k_0, ..., k_{N-1}}`에 대해:

   ```
   rank(k_i) = i
   ```

2. 각 PLR segment는 특정 key interval에서 다음 식으로 rank를 근사한다.

   ```
   rank(k) ~= slope * k + intercept
   ```

3. Greedy PLR fitting:
   - Sorted keys를 한 번 scan한다.
   - Prediction error bound 안에 들어오는 동안 segment를 확장한다.
   - Error bound를 벗어나면 segment를 닫고 다음 segment를 시작한다.

4. PLR inverse:
   - Materialization 시 local rank `i`에 대해 key를 추정한다.
   - Strictly increasing key sequence가 되도록 후처리한다.

### 논문에서 수식으로 넣을 최소 내용

```
pos_j(k) = a_j k + b_j, for k in [l_j, r_j]
```

그리고 PLR model이 `O(#segments)` metadata로 `O(#keys)` sorted run을 표현한다는
점을 명시한다.

## 4. Virtual Flush: Creating L0 vSSTs

이 subsection은 기존 insert/flush path를 F2Load가 어떻게 대체하는지 설명한다.

### 핵심 메시지

- Conventional loading은 memtable write와 flush를 통해 L0 SSTable을 만든다.
- F2Load는 synthetic key sequence를 batch 단위로 생성하고, 각 batch를 하나의
  L0 vSST로 만든다.
- 이 단계는 physical memtable과 flush I/O를 거치지 않지만, 결과적으로 L0에
  들어갈 sorted run descriptor를 만든다.

### 현재 구현 흐름

1. Generate `uint64_t` keys.
2. Store keys in a flat vector.
3. Radix-sort the vector.
4. Remove intra-batch duplicates.
5. Fit a PLR model.
6. Build global and range-local KMV sketches from the sorted unique keys.
7. Create a virtual L0 SST descriptor.
8. Enqueue it into the pending L0 window.

### 구현 세부사항으로 적을 것

- `db_bench fillvirtual` path에 구현되어 있다.
- Random key generation과 sorting은 foreground Phase 1에서 수행된다.
- vSST의 estimated size는 `num_entries * average_entry_size`로 계산된다.
- KMV sketch는 sorted unique key vector에서 가장 작은 hash sample을 유지한다.
  모든 vcomp 실험은 고정된 total budget `512`와 range-local sketch `8`개를
  사용한다.

## 5. Visible L0 Window

이 subsection은 F2Load가 왜 생성된 모든 L0 vSST를 한 번에 VersionSet에
등록하지 않는지 설명한다. 이 부분은 현재 디자인의 핵심이다.

### 핵심 메시지

- 모든 generated L0 vSST를 즉시 visible하게 만들면 L0 pressure가 비정상적으로
  커지고, RocksDB의 compaction dynamics와 달라진다.
- F2Load는 pending L0 queue와 visible L0 byte target을 분리한다.
- VersionSet에 등록된 vSST만 RocksDB picker-visible file이 된다.

### 현재 refill rule

```
virtual_l0_visible_bytes_ < target_bytes
```

### 설명할 동작

1. Virtual flush는 vSST를 pending queue에 넣는다.
2. `RefillVirtualL0Window()`는 visible L0 byte가 target보다 작을 때만 pending
   vSST를 VersionSet에 등록한다.
3. 등록은 bounded VersionEdit batch로 수행된다.
4. Virtual compaction commit이 L0 input/output bytes를 반영하면, commit path가
   다시 visible L0 window를 refill한다.

### 현재 기본값

| Parameter | Default | Meaning |
|-----------|---------|---------|
| `--vcomp_visible_l0_batch_mb` | `0` | Visible L0 byte target; zero uses `max_compaction_bytes` |
| `--vcomp_register_batch_max` | `256` | Max files per registration VersionEdit |

### 논문에서 주의할 표현

- "vSSTs become visible when registered into VersionSet"이라고 쓰는 것이 정확하다.
- 별도 eligibility bit이나 별도 background release mechanism을 설명하지 않는다.
- 핵심은 "bounded visible L0 window"와 "byte-targeted refill"이다.

## 6. Virtual Compaction

이 subsection은 F2Load의 핵심 algorithmic contribution이다. 실제 compaction
merge를 어떻게 descriptor merge로 바꾸는지 설명한다.

### 핵심 메시지

- RocksDB는 기존과 같이 compaction input을 선택한다.
- 선택된 input 중 하나라도 vSST이면 F2Load는 virtual compaction path를 실행한다.
- Virtual compaction은 key-value record를 merge하지 않고, PLR rank functions를
  merge한다.

### Dispatch rule

```
if any input file is in VirtualSSTRegistry:
    RunVirtualCompaction()
else:
    Run normal CompactionJob()
```

이 dispatch guard는 materialization 이후에도 중요하다. Final real SSTable로
교체된 뒤에는 같은 RocksDB instance에서 일반 compaction이 선택될 수 있으므로,
input이 registry에 존재할 때만 virtual path를 타야 한다.

### PLR merge intuition

두 sorted runs `A`와 `B`를 merge한 run `C`에서 key `k`의 rank는:

```
rank_C(k) = rank_A(k) + rank_B(k)
```

각 input rank function이 linear segment로 표현되어 있으면:

```
rank_A(k) = a_A k + b_A
rank_B(k) = a_B k + b_B
rank_C(k) = (a_A + a_B) k + (b_A + b_B)
```

따라서 compaction merge는 input PLR segments의 boundary union을 만들고, 각
interval에서 active model들의 slope/intercept를 합산하는 방식으로 수행된다.

### KMV-based dedup cardinality

PLR merge만으로는 output key distribution의 shape는 만들 수 있지만, duplicate
key cardinality를 안전하게 알 수 없다. 이전 구현은 active PLR segment의 slope를
key density로 보고 다음 inclusion-exclusion 식으로 cross-SST dedup을 추정했다.

```
dedup slope = 1 - Π(1 - slope_i)
```

이 방식은 실제 key identity를 보지 않기 때문에, unique workload에서도 같은 key
range 안에 density가 겹치면 duplicate가 있다고 오판할 수 있다. 특히 여러 번
merge된 deep-level vSST에서는 작은 오차가 누적되어 final materialized key count가
줄어드는 문제가 발생했다.

현재 구현은 PLR을 shape/interval construction에만 사용하고, dedup cardinality는
KMV만으로 결정한다.

| Component | Role |
|-----------|------|
| PLR model | merged key->rank shape 및 key interval boundary 구성 |
| Global KMV sketch | output logical entry count 및 total dedup cardinality 추정 |
| Range-local KMV sketch | interval-local mass distribution 추정 |

KMV(K-Minimum Values)는 key를 hash한 뒤 가장 작은 hash sample과 `theta_hash`를
저장한다. 현재 구현은 vSST 전체 sketch와 함께 `kmv_ranges`를 유지한다. L0 vSST는
sorted key sequence를 여러 key range로 나누고, total KMV sample budget을 range에
분배한다. Compaction output vSST도 input range sketches를 해당 output key range로
merge하여 다시 range-local sketch를 가진다.

Compaction 시 PLR segment boundaries로 key interval을 만들되, dedup cardinality는
PLR slope를 사용하지 않는다. 전체 output entry 수는 input vSST들의 global KMV
union으로 먼저 결정한다. 이후 각 interval에서는 겹치는 input range sketch만 union해
raw interval mass를 추정하고, 모든 interval mass의 합이 global KMV total과 맞도록
rescale한다.

```
naive_entries = Σ input.num_entries
kmv_total_entries ~= KMVUnion(input.global_kmv)

for each interval I:
  active = {vSST | vSST overlaps I}
  ranges = {r | r in active.kmv_ranges and r overlaps I}
  raw_interval_entries[I] ~= KMVUnion(ranges filtered to I)

scale = kmv_total_entries / Σ raw_interval_entries
interval_entries[I] = raw_interval_entries[I] * scale
dedup_entries = naive_entries - kmv_total_entries
```

PLR slope inclusion-exclusion은 dedup cardinality에 사용하지 않는다. Interval 안의
rank shape는 input PLR slope를 사용해 유지하지만, total mass는 global KMV estimate에
맞추고 range-local KMV는 그 mass를 key interval에 분배하는 데만 사용한다.

### KMV implementation details

- L0 vSST 생성 시 sorted unique key vector에서 global KMV와 range-local KMV를
  함께 만든다.
- Sketch sample은 `(key, hash)`를 저장한다. `key`를 함께 저장하는 이유는 output
  vSST split 후 각 output range에 속한 sample만 전파하기 위해서이다.
- Sample count는 `512`, range bucket 수는 `8`로 고정한다. 이 값은 실험
  파라미터가 아니며 모든 vcomp 실험에서 동일하게 사용한다.
- Sample budget은 vSST 전체 기준으로 유지하고 bucket들이 나눠 쓴다.
- `theta_hash`를 함께 유지해 range-filtered sample을 bottom-K로 오해하지 않도록
  한다.
- Global KMV union estimate가 output logical entry count를 직접 결정한다.
- Output vSST는 input range sketches를 key range로 filter/merge한 global sketch와
  range-local sketches를 다시 가진다.
- PLR 기반 dedup fallback은 없다. 어떤 interval에서 KMV sample이 없으면 PLR
  density로 보정하지 않는다.

### Virtual compaction 단계

1. Input file numbers로 vSST descriptor 조회.
2. Input PLR boundaries를 union하여 merged intervals 구성.
3. Input vSST들의 global KMV union으로 output logical entry count 계산.
4. 각 interval에서 active vSST들의 range-local KMV sample로 raw interval mass 추정.
5. Raw interval mass를 global KMV total에 맞게 rescale하고 merged PLR rank function 구성.
6. Merged model을 output vSST들로 split하고 output KMV/range sketches 전파.
7. VersionEdit으로 input vSST 삭제, output vSST 추가.

## 7. Preserving LSM Tree Shape

이 subsection은 F2Load가 단순히 key set만 맞추는 것이 아니라, 왜 tree shape를
맞춰야 하는지 설명한다.

### 핵심 메시지

- Benchmark realism은 final key set만으로 결정되지 않는다.
- LSM tree의 file count, file size distribution, level assignment, key-range
  overlap은 read amplification과 filter access pattern에 직접 영향을 준다.
- F2Load는 RocksDB compaction output splitting rule을 모방하여 output vSST
  boundary를 결정한다.

### Output splitting에서 고려하는 요소

| Factor | Why it matters |
|--------|----------------|
| Target file size | Baseline SST size distribution matching |
| Bottommost level | RocksDB uses different max output size behavior |
| Grandparent boundaries | Controls future overlap and compaction shape |
| Dynamic threshold | Matches RocksDB `ShouldStopBefore` behavior |
| 2x non-bottom cap | Matches RocksDB max output file size rule |

### 설명 순서

1. Merged PLR model은 하나의 큰 sorted run을 표현한다.
2. 이 run을 target level의 여러 output vSST로 나누어야 한다.
3. Size-only split은 baseline과 다른 overlap pattern을 만들 수 있다.
4. F2Load는 grandparent file boundaries를 PLR position으로 변환한다.
5. Size cuts와 grandparent boundary cuts를 함께 scan한다.
6. RocksDB와 유사하게 dynamic threshold를 50%에서 90%까지 증가시킨다.

### 논문에 들어갈 표현

"F2Load treats output splitting as part of correctness, not merely as an
optimization. The materialized database must expose a read path similar to a
database produced by normal loading."

## 8. Materialization

이 subsection은 F2Load의 중간 상태가 어떻게 일반 RocksDB database로 변환되는지
설명한다.

### 핵심 메시지

- Loading 중에는 vSST만 존재하지만, benchmark 전에 모든 vSST는 real SSTable로
  변환된다.
- Materialization은 vSST별로 독립적이므로 병렬화가 쉽다.
- Materialization 후에는 F2Load-specific runtime이 필요 없다.

### Materialization 단계

1. Registry에서 final vSST 목록을 가져온다.
2. 각 vSST에 대해 PLR inverse walk로 key sequence를 복원한다.
3. Strictly increasing key order를 보장하도록 보정한다.
4. `SstFileWriter`로 real SSTable을 작성한다.
5. 하나의 VersionEdit으로 virtual files를 삭제하고 real files를 추가한다.

### 논문에서 강조할 점

- Real SSTable은 RocksDB의 normal SST file format이다.
- Read benchmark는 기존 RocksDB read path를 그대로 사용한다.
- F2Load는 loading-only acceleration이다.

## 9. RocksDB Implementation

이 subsection은 디자인이 실제 RocksDB 코드에서 어디에 구현되었는지 설명한다.
논문에서는 너무 많은 file path를 나열하기보다, modification point를 세
그룹으로 묶는 것이 읽기 좋다.

### 9.1 Loader integration

설명할 내용:

- `db_bench`에 `fillvirtual` benchmark 추가.
- Synthetic key generation, radix sort, PLR fitting, pending queue enqueue를
  foreground phase에서 수행.
- BG compaction drain 후 materialization 수행.

### 9.2 Metadata integration

설명할 내용:

- `DBImpl`이 `VirtualSSTRegistry`와 pending L0 window state를 유지.
- `RegisterVirtualL0File()`이 vSST를 RocksDB file metadata로 등록.
- `RefillVirtualL0Window()`가 byte-targeted visible L0 window를 유지.
- VersionSet은 virtual file에 대해 physical table loading과 verification을
  건너뜀.

### 9.3 Compaction integration

설명할 내용:

- `BackgroundCompaction`에서 registry membership으로 virtual/real path 결정.
- `RunVirtualCompaction()`이 PLR merge와 output split을 수행.
- Virtual compaction result는 VersionEdit으로 VersionSet에 반영.
- Input이 모두 real SSTable이면 기존 `CompactionJob` path를 그대로 사용.

## 10. Metadata-Path Optimizations

이 subsection은 F2Load가 compaction I/O를 제거한 뒤 새롭게 드러난 metadata
bottleneck을 어떻게 줄였는지 설명한다.

### 핵심 메시지

- Virtual compaction은 data I/O를 제거하지만 VersionSet update는 여전히
  필요하다.
- 대규모 dataset에서는 manifest update, VersionBuilder, compaction score
  recomputation이 새로운 bottleneck이 된다.
- F2Load는 correctness에 영향을 주지 않는 metadata-path 최적화만 적용한다.

### 현재 구현된 최적화

| Optimization | Purpose |
|--------------|---------|
| BG commit batching | Multiple virtual compaction results per manifest group |
| `VCOMP_BG_COMMIT_DELAY_US=100` | Allows near-ready follower jobs to join batch |
| Deferred obsolete cleanup | Avoids per-job obsolete file scan/purge |
| Unchanged-level fast path | Avoids rebuilding unchanged level file vectors one file at a time |
| Per-level score input reuse | Avoids repeated full-file scans on Version append path |

### 현재 기본값

```
VCOMP_BG_COMMIT_BATCH_MAX=16
VCOMP_BG_COMMIT_DELAY_US=100
```

## 11. Realism and Scope

이 subsection은 reviewer가 물을 수 있는 "무엇이 realistic한가?"와 "무엇은
재현하지 않는가?"를 미리 분리해서 설명한다.

### F2Load가 보존하는 것

- Final key set
- Final RocksDB SSTable format
- LSM level assignment
- File key ranges
- File size accounting
- RocksDB compaction picker decision path
- Manifest/VersionSet evolution for registered files

### F2Load가 fast-forward하는 것

- Memtable insert CPU cost
- Intermediate SSTable data write
- Repeated compaction input read
- Repeated compaction output write
- Loading-time write amplification

### 중요한 claim boundary

F2Load는 loading process 자체의 모든 side effect를 재현하려는 시스템이 아니다.
F2Load의 목적은 KVS benchmark를 시작하기 전에 필요한 final dataset을 빠르게
생성하는 것이다. 따라서 loading 중 발생했을 temporary disk state, cache state,
intermediate write amplification은 의도적으로 제거한다. 대신 final dataset의
logical content와 LSM tree structure를 현실적으로 유지한다.

## 12. Recommended Section Flow for the Paper

논문에는 다음 순서로 쓰는 것이 가장 읽기 쉽다.

1. **Overview**: F2Load가 compaction I/O를 fast-forward하는 전체 pipeline.
2. **vSST abstraction**: physical SSTable을 대체하는 descriptor 정의.
3. **PLR modeling**: sorted run을 learned rank function으로 표현.
4. **Virtual flush and L0 window**: L0 vSST 생성과 byte-targeted visible window.
5. **Virtual compaction**: PLR rank function merge.
6. **Tree-shape preservation**: output splitting과 grandparent boundary handling.
7. **Materialization**: final real SSTable 생성.
8. **RocksDB implementation**: loader, metadata, compaction integration.
9. **Optimizations**: metadata bottleneck 완화.
10. **Scope and realism**: preserved semantics와 fast-forwarded cost 구분.
