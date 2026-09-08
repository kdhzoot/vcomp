#!/usr/bin/env python3
"""Compare retained run2 bitmap audit with the validated seq-fix rerun."""
import hashlib
import json
from pathlib import Path

ARTIFACTS = Path(__file__).resolve().parent.parent
OLD = ARTIFACTS / 'fidelity_100gib_20260908_run2'
NEW = ARTIFACTS / 'fidelity_100gib_20260908_seqfix_run3'


def read(path):
    return json.loads(path.read_text())


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(8 * 1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    assert read(NEW / 'status.json')['status'] == 'completed'
    assert (NEW / 'full/COMPLETED').is_file()
    output = NEW / 'full/run2_comparison'
    assert not output.exists(), 'preserve any existing comparison'
    new_results = read(NEW / 'full/results.json')
    assert len(new_results) == 12 and all(r['status'] == 'validated' for r in new_results)
    manifest = read(NEW / 'manifest.json')
    measured_hashes = {name: digest(NEW / 'bin' / name) for name in manifest['binary_sha256']}
    assert measured_hashes == manifest['binary_sha256'], 'frozen executable changed during campaign'
    old_summary = {r['case_id']: r for r in read(OLD / 'full/diagnostics_20260908/distinct_summary.json')['cases']}
    old_traces = {r['case_id']: r for r in read(OLD / 'full/trace_manifest.json')}
    new_traces = {r['case_id']: r for r in read(NEW / 'full/trace_manifest.json')}
    rows = []
    source_files = [OLD / 'full/diagnostics_20260908/distinct_summary.json',
                    OLD / 'full/trace_manifest.json', NEW / 'full/trace_manifest.json',
                    NEW / 'full/results.json', NEW / 'manifest.json', NEW / 'seqfix_source_hashes.json']
    for r in new_results:
        if r['system'] != 'f2load':
            assert r['exact_unique_keys'] == r['expected_unique_keys'] and r['fidelity_matches'] is True
            continue
        case = r['case_id']
        old = old_summary[case]
        exact_path = NEW / 'full/cases' / (case + '_f2load') / 'exact_cardinality.json'
        exact = read(exact_path)
        old_audit_path = OLD / 'full/diagnostics_20260908' / (case + '_f2load.distinct_audit.json')
        old_audit = read(old_audit_path)
        source_files += [exact_path, old_audit_path]
        assert exact['status'] == 'ok' and exact['strict_increasing'] is True
        assert exact['estimated_pending_compaction_bytes'] == 0
        assert old_audit['status'] == 'ok' and old_audit['distinct_count_complete'] is True
        row = dict(case_id=case, expected_unique=r['expected_unique_keys'],
                   trace_sha256_match=old_traces[case]['trace_sha256'] == new_traces[case]['trace_sha256'],
                   trace_sha256_old=old_traces[case]['trace_sha256'], trace_sha256_new=new_traces[case]['trace_sha256'],
                   old_D=old['descriptor_entries'], new_D=r['stage1_live_descriptor_entries'],
                   old_M=old['materialized_entries'], new_M=r['stage2_live_sst_keys_written'],
                   old_U=old_audit['distinct_keys'], new_U=exact['exact_unique_keys'],
                   old_iterator_rows=old_audit['iterator_rows'], new_iterator_rows=exact['exact_unique_keys'],
                   old_duplicate_rows=old_audit['duplicate_rows'], old_equal_adjacent=old_audit['equal_adjacent'],
                   old_decreasing_adjacent=old_audit['decreasing_adjacent'],
                   new_non_increasing_count=exact['non_increasing_count'],
                   new_strict_increasing=exact['strict_increasing'],
                   new_physical_sst_entry_sum=exact['sst_entry_sum'],
                   new_pending_compaction_bytes=exact['estimated_pending_compaction_bytes'])
        row.update(old_D_minus_M=row['old_D']-row['old_M'], new_D_minus_M=row['new_D']-row['new_M'],
                   old_U_error_percent=100*(row['old_U']-row['expected_unique'])/row['expected_unique'],
                   new_U_error_percent=100*(row['new_U']-row['expected_unique'])/row['expected_unique'])
        rows.append(row)
    assert len(rows) == 6
    report = dict(old_run=str(OLD), new_run=str(NEW), cases=rows,
                  all_trace_sha256_match=all(r['trace_sha256_match'] for r in rows),
                  new_all_twelve_validated=True, new_baseline_all_exact=True,
                  single_corrected_full_matrix=True, frozen_binary_sha256_unchanged=True,
                  frozen_binary_sha256=measured_hashes,
                  new_f2_all_strict_increasing=all(r['new_strict_increasing'] for r in rows),
                  provenance={str(p): digest(p) for p in source_files},
                  count_semantics={'D':'live descriptor entries before materialization',
                                   'M':'live SST entries at materialization',
                                   'old_U':'independent complete bitmap distinct audit; original exact_unique_keys was iterator rows',
                                   'new_U':'complete clean iterator count after strict ordering validated'},
                  limitations=['Concurrent scheduling can change virtual compaction outcomes between campaigns.',
                               'The sequence fix addresses iterator visibility, not descriptor/materialization approximation.',
                               'Matching cardinality does not establish matching key sets or values.',
                               'Elapsed times are operational only; this is not an isolated performance comparison.'])
    output.mkdir()
    (output / 'comparison.json').write_text(json.dumps(report, indent=2, sort_keys=True)+'\n')
    lines = ['# 100GiB seq 수정 재실험: run2와 run3 비교', '',
             'Sequence/iterator 정합성만 수정한 binary로 1GiB pilot 후 100GiB full matrix를 1회 실행했다. '
             '각 단계에서 baseline 6개와 F2 6개를 동시에 시작했고 12/12 검증을 완료했다. '
             'Full baseline 6개는 trace의 unique 수와 일치하며, 수정 F2 6개는 clean RocksDB iterator의 strict increasing 검사를 통과했다.', '',
             'D는 생성 전 live descriptor 엔트리 합, M은 materialization 직후 live SST 엔트리 합, U는 최종 visible distinct key 수다. '
             'run2의 U는 독립 bitmap 감사 결과를 사용한다. 순서 검사가 실패한 run2의 기존 `exact_unique_keys`는 iterator 행 수이므로 U로 사용하지 않았다.', '',
             '| 조건 | D 기존 → 수정 | M 기존 → 수정 | U 기존 → 수정 | U 상대오차 기존 → 수정 |',
             '|---|---:|---:|---:|---:|']
    for r in rows:
        lines.append(f"| {r['case_id']} | {r['old_D']:,} → {r['new_D']:,} | {r['old_M']:,} → {r['new_M']:,} | {r['old_U']:,} → {r['new_U']:,} | {r['old_U_error_percent']:+.3f}% → {r['new_U_error_percent']:+.3f}% |")
    lines += ['', '| 조건 | 기존 중복 iterator 행 | 수정 non-increasing 행 | 수정 pending bytes | trace SHA-256 일치 |',
              '|---|---:|---:|---:|---|']
    for r in rows:
        lines.append(f"| {r['case_id']} | {r['old_duplicate_rows']:,} | {r['new_non_increasing_count']:,} | {r['new_pending_compaction_bytes']:,} | {r['trace_sha256_match']} |")
    lines += ['', '같은 입력 trace를 사용했어도 동시 실행 스케줄에 따라 가상 compaction 결과 D/M/U는 달라질 수 있다. '
              '따라서 두 대규모 캠페인의 차이를 sequence 수정 단독의 정확도 효과로 해석하지 않는다. '
              'Sequence 교정 자체는 앞선 동일 생성 배치의 128MiB old/new 파일럿에서 별도로 확인했다. '
              '이번 실행은 수정 경로가 여섯 100GiB 조건에서 정상 iterator를 제공하는지와 남아 있는 cardinality 오차를 측정한다.', '',
              'Baseline 적재는 F2 적재와 동일한 캠페인별 frozen `f2_db_bench`에서 `baseload`와 `use_virtual_compaction=false`를 사용한다. '
              '후속 settling은 고정 clean RocksDB executable, 최종 scan은 clean RocksDB 정적 라이브러리에 연결한 checker를 사용한다.', '',
              '이 baseline/settle/reader 구성은 이전 runner 그대로다. 이번 측정 도중 실행 binary를 변경하지 않았으며, '
              '완료 시 frozen executable SHA-256을 manifest와 다시 대조했다. Descriptor/materialization 정확도 수정은 이번 실행에 포함하지 않았다.', '',
              '입력 trace 6개 SHA-256의 run2/run3 일치 여부와 원본 JSON 해시는 `comparison.json`에 보존했다. '
              '기존 run2 DB/trace/결과를 변경하지 않았고, 재실험 DB와 trace는 별도 run3 `/work` 경로에 생성했다. '
              '명령·환경·실행 상태·소스/바이너리 해시는 run3 manifest, case별 로그 및 `seqfix_source_hashes.json`에서 확인할 수 있다. '
              '시간은 동시 실행의 운영 기록이며 성능 비교 수치로 사용하지 않는다.']
    (output / 'REPORT.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'output':str(output),'all_trace_sha256_match':report['all_trace_sha256_match'],'cases':rows}, indent=2))


if __name__ == '__main__':
    main()
