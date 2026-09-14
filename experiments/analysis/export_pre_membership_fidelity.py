#!/usr/bin/env python3
"""Recompute fidelity metrics for the original baseline 10 and PLR f01-f10."""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics as st

EXP = Path(__file__).resolve().parents[1]
RESULTS = EXP / 'results'
BASE = ['b01', 'b02', 'b03', 'n01', 'n02', 'n03', 'n04', 'r01', 'r02', 'run3']
F2 = [f'f{i:02d}' for i in range(1, 11)]
METRICS = {
    'throughput_ops_sec': 'ops/s', 'avg_latency_us': 'us',
    'filter_checks_per_get': 'checks/Get', 'positive_lookup_pct': '%',
    'bloom_false_positive_pct': '%', 'data_cache_misses_per_op': 'misses/op',
    'data_cache_hit_pct': '%', 'compaction_read_bytes': 'bytes',
    'compaction_write_bytes': 'bytes', 'compaction_read_bytes_per_op': 'bytes/op',
    'compaction_write_bytes_per_op': 'bytes/op', 'flush_write_bytes_per_op': 'bytes/op',
    'device_read_ios': 'I/Os', 'device_write_ios': 'I/Os',
    'device_read_bytes': 'bytes', 'device_write_bytes': 'bytes',
    'device_read_bytes_per_op': 'bytes/op', 'device_write_bytes_per_op': 'bytes/op',
}
PCT_METRICS = {k for k in METRICS if k.endswith('_pct')}


def tsv(path, rows):
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--keyset-audit', type=Path)
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    sources, refs, records, tails = {}, {}, [], []

    def record(path):
        path = Path(path).resolve()
        sources[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()

    def diskstats(path):
        record(path)
        f = next(line.split() for line in path.read_text().splitlines()
                 if len(line.split()) >= 14 and line.split()[2] == 'md0')
        return dict(device_read_ios=int(f[3]), device_write_ios=int(f[7]),
                    device_read_bytes=int(f[5])*512, device_write_bytes=int(f[9])*512)

    for system, tags, prefix in [('baseline', BASE, 'ycsb_band_'),
                                  ('f2load', F2, 'ycsb_f2band_')]:
        for arm in tags:
            paths = list(RESULTS.glob(prefix + arm + '_*/results.json'))
            assert len(paths) == 1
            path = paths[0]
            record(path)
            cells = [x for x in json.loads(path.read_text())
                     if x['phase'] == 'full' and x['system'] == system]
            assert len(cells) == 6
            assert {x['workload'][-1].upper() for x in cells} == set('ABCDEF')
            for name in ['manifest.json', 'provenance/sources.json',
                         'provenance/source_identities.json']:
                p = path.parent / name
                if p.exists():
                    record(p)
            for x in cells:
                assert x['status'] == 'ok' and x['exit_code'] == 0
                assert not x['timed_out'] and not x['missing_tickers'] and not x['warnings']
                assert x['threads'] == 48
                command_path = path.parent / 'evidence/full' / x['workload'] / system / 'command.json'
                record(command_path)
                command = json.loads(command_path.read_text())
                options = {a.split('=', 1)[0]: a.split('=', 1)[1]
                           for a in command[1:] if a.startswith('--') and '=' in a
                           and a.split('=', 1)[0] not in ['--db', '--report_file']}
                w = x['workload'][-1].upper()
                refs.setdefault(w, (options, x['binary_sha256']))
                assert (options, x['binary_sha256']) == refs[w], (arm, w)
                assert options['--cache_size'] == str(50*1024**3)
                assert options['--duration'] == '300'
                t = x['tickers']
                ops, gets, found = x['operations'], x['engine_keys_read'], x['successful_gets']
                useful = t['rocksdb.bloom.filter.useful']
                full = t['rocksdb.bloom.filter.full.positive']
                true = t['rocksdb.bloom.filter.full.true.positive']
                fp = full - true
                assert t['rocksdb.bloom.filter.prefix.checked'] == 0
                assert 0 <= found <= gets and fp >= 0 and ops > 0
                assert gets == t['rocksdb.number.keys.read']
                assert found == sum(t[k] for k in ['rocksdb.memtable.hit', 'rocksdb.l0.hit',
                                                 'rocksdb.l1.hit', 'rocksdb.l2andup.hit'])
                row = dict(system=system, arm=arm, workload=w, operations=ops,
                           get_requests=gets, successful_gets=found,
                           bloom_useful=useful, bloom_full_positive=full,
                           bloom_full_true_positive=true,
                           throughput_ops_sec=x['throughput_ops_sec'], avg_latency_us=x['avg_latency_us'],
                           filter_checks_per_get=(useful+full)/gets if gets else None,
                           positive_lookup_pct=100*found/gets if gets else None,
                           bloom_false_positive_pct=100*fp/(useful+fp) if gets and useful+fp else None,
                           data_cache_misses_per_op=x['data_cache_miss']/ops,
                           data_cache_hit_pct=100*x['data_cache_hit_fraction']
                               if x['data_cache_hit_fraction'] is not None else None,
                           compaction_read_bytes=x['compaction_read_bytes'],
                           compaction_write_bytes=x['compaction_write_bytes'],
                           compaction_read_bytes_per_op=x['compaction_read_bytes']/ops,
                           compaction_write_bytes_per_op=x['compaction_write_bytes']/ops,
                           flush_write_bytes_per_op=x['flush_write_bytes']/ops)
                raw = Path(x['log_dir']) / 'raw'
                a, b = diskstats(raw/'diskstats.start'), diskstats(raw/'diskstats.end')
                delta = {k: b[k]-a[k] for k in a}
                assert all(v >= 0 for v in delta.values())
                row.update(delta)
                row.update(device_read_bytes_per_op=delta['device_read_bytes']/ops,
                           device_write_bytes_per_op=delta['device_write_bytes']/ops,
                           source_run=path.parent.name, source_db=x['source_db_dir'],
                           measured_seconds=x['measured_seconds'],
                           process_elapsed_seconds=x['process_elapsed_sec'],
                           measurement_binary_sha256=x['binary_sha256'])
                records.append(row)
                for operation, h in x['operation_histograms'].items():
                    tails.append(dict(system=system, arm=arm, workload=w, operation=operation,
                        count=h['count'], mean_us=h['average_us'],
                        p50_us=h['p50_us'], p99_us=h['p99_us']))
    assert len(records) == 120
    assert len({(r['system'],r['source_db']) for r in records}) == 20
    tsv(out/'behavior_raw.tsv', records)
    tsv(out/'operation_latency_raw.tsv', tails)
    structure, levels = [], []
    for system, tags in [('baseline', BASE), ('f2load', F2)]:
        for arm in tags:
            selected = [r for r in records if r['system']==system and r['arm']==arm]
            assert len(selected)==6
            run = selected[0]['source_run']
            original = json.loads((RESULTS/run/'results.json').read_text())
            cs = [r for r in original if r['phase']=='full' and r['system']==system]
            identities = []
            for row in cs:
                p=Path(row['log_dir'])/'source_identity.json'
                record(p)
                identities.append(json.loads(p.read_text()))
            assert all(i==identities[0] for i in identities)
            c=next(r for r in cs if r['workload']=='workloadc')
            assert all(c[k]==0 for k in ['engine_keys_written','compaction_read_bytes',
                                         'compaction_write_bytes','flush_write_bytes'])
            ssts=identities[0]['ssts']
            n,size=len(ssts),sum(v[1] for v in ssts.values())
            assert sum(v['files'] for v in c['final_levels'].values())==n
            structure.append(dict(system=system,arm=arm,final_sst_count=n,
                             final_sst_bytes=size,average_sst_bytes=size/n,
                             source_run=run,source_db=c['source_db_dir']))
            for level,info in sorted(c['final_levels'].items(),key=lambda x:int(x[0])):
                levels.append(dict(system=system,arm=arm,level=int(level),
                                   files=info['files'],rounded_size_mib=info['size_mib']))
    assert len(structure)==20 and len(levels)==140
    tsv(out/'structure_raw.tsv',structure)
    tsv(out/'levels_raw.tsv',levels)
    structure_summary=[]
    for metric in ['final_sst_count','final_sst_bytes','average_sst_bytes']:
        vals={s:[r[metric] for r in structure if r['system']==s] for s in ['baseline','f2load']}
        b,f=vals['baseline'],vals['f2load']
        structure_summary.append(dict(metric=metric,baseline_n=10,baseline_mean=st.mean(b),
             baseline_min=min(b),baseline_max=max(b),f2load_n=10,f2load_mean=st.mean(f),
             f2load_min=min(f),f2load_max=max(f),mean_difference_pct=100*(st.mean(f)/st.mean(b)-1)))
    tsv(out/'structure_summary.tsv',structure_summary)
    levels_summary=[]
    for level in range(7):
        for metric in ['files','rounded_size_mib']:
            vals={s:[r[metric] for r in levels if r['system']==s and r['level']==level]
                  for s in ['baseline','f2load']}
            b,f=vals['baseline'],vals['f2load']
            levels_summary.append(dict(level=level,metric=metric,baseline_n=10,
                 baseline_mean=st.mean(b),baseline_min=min(b),baseline_max=max(b),
                 f2load_n=10,f2load_mean=st.mean(f),f2load_min=min(f),f2load_max=max(f)))
    tsv(out/'levels_summary.tsv',levels_summary)
    summaries = {}
    for label, exclude in [('behavior_summary', set()), ('sensitivity_without_f01', {'f01'})]:
        summary = []
        for w in 'ABCDEF':
            for metric, unit in METRICS.items():
                vals = {s: [r[metric] for r in records if r['system'] == s
                            and r['workload'] == w and r['arm'] not in exclude
                            and r[metric] is not None] for s in ['baseline', 'f2load']}
                b, f = vals['baseline'], vals['f2load']
                if not b:
                    assert not f
                    continue
                bm, fm = st.mean(b), st.mean(f)
                summary.append(dict(workload=w, metric=metric, unit=unit,
                    baseline_n=len(b), baseline_mean=bm, baseline_min=min(b),
                    baseline_max=max(b), baseline_sd=st.stdev(b),
                    f2load_n=len(f), f2load_mean=fm, f2load_min=min(f),
                    f2load_max=max(f), f2load_sd=st.stdev(f),
                    absolute_mean_difference=fm-bm,
                    mean_difference_pct=100*(fm/bm-1) if bm else None,
                    percentage_point_difference=fm-bm if metric in PCT_METRICS else None,
                    f2load_mean_in_baseline_range=min(b) <= fm <= max(b)))
        tsv(out/(label+'.tsv'), summary)
        summaries[label] = summary
    keyset=None
    if args.keyset_audit:
        record(args.keyset_audit)
        keyset=json.loads(args.keyset_audit.read_text())
        assert keyset['baseline_cohort_arm']=='run3' and keyset['f2load_cohort_arm']=='f06'
        assert keyset['baseline_unique_keys']-keyset['intersection_keys']==keyset['missing_keys']
        assert keyset['f2load_unique_keys']-keyset['intersection_keys']==keyset['invented_keys']
        (out/'keyset_f06_audit.json').write_text(json.dumps(keyset,indent=2)+'\n')
    record(__file__)
    manifest = dict(created_utc=datetime.now(timezone.utc).isoformat(),
                    purpose='Historical metric recomputation; no benchmark or database scan.',
                    input_gib=1000, key_bytes=24, value_bytes=1000,
                    baseline_arms=BASE, f2load_arms=F2, primary_exclusions=[],
                    sensitivity_exclusions=['f01'], rows=120,
                    workload_options={w: v[0] for w,v in refs.items()},
                    measurement_binary_sha256=refs['A'][1], sources=sources)
    (out/'source_manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
    plot(out, records)
    report(out,summaries,structure_summary,levels_summary,keyset)
    for row in summaries['behavior_summary']:
        if row['metric'] in ['throughput_ops_sec','avg_latency_us','filter_checks_per_get',
                             'positive_lookup_pct','compaction_write_bytes_per_op']:
            print(row['workload'], row['metric'], round(row['baseline_mean'],6),
                  round(row['f2load_mean'],6), row['f2load_mean_in_baseline_range'])


def report(out,summaries,structure,levels,keyset):
    def fmt(v):
        if abs(v)>=1e9:return f'{v/1e9:.3f}G'
        if abs(v)>=1e6:return f'{v/1e6:.3f}M'
        if abs(v)>=1e3:return f'{v/1e3:.3f}K'
        return f'{v:.4f}'
    lines=['# Membership 이전 Baseline 10개와 F2Load f01-f10 fidelity',
           '', '저장된 결과를 재계산했습니다. 신규 로딩·YCSB·DB 스캔은 실행하지 않았습니다.',
           'RocksDB 기반 비교이며, 입력 1000 GiB, KV 24+1000 B, 48 threads, 50 GiB cache, workload별 300초입니다.',
           'Baseline은 b01,b02,b03,n01,n02,n03,n04,r01,r02,run3, F2Load는 최초 f01-f10입니다. 선별된 후속 10개 또는 membership 적용 e01-e10을 섞지 않았습니다.',
           '', '## 평균과 변동 범위', '',
           '각 arm을 동일 가중치로 평균했습니다. 범위는 최솟값–최댓값이며 신뢰구간이 아닙니다. 두 집합은 paired trial로 취급하지 않습니다.',
           '서버 간섭이 기록된 f01도 주 비교(n=10)에 포함했습니다. f01 전체를 제외한 n=9 민감도 분석은 sensitivity_without_f01.tsv에 따로 제공합니다.',
           '', '| 지표 | Baseline 평균 | F2Load 평균 | 차이 |', '|---|---:|---:|---:|']
    for r in structure:
        lines.append(f"| {r['metric']} | {fmt(r['baseline_mean'])} | {fmt(r['f2load_mean'])} | {r['mean_difference_pct']:+.3f}% |")
    lines += ['', 'SST 총크기는 바이트 단위, 파일당 평균 크기는 각 arm의 bytes/count를 평균한 값입니다. K/M/G는 십진수입니다.',
              'SST 수·총크기는 각 arm의 source_identity.json에서 계산했습니다. 모든 A–F에서 identity가 같고 C에서 쓰기·flush·compaction이 0임을 확인했습니다.',
              '레벨별 파일 수는 정확한 정수이며 크기는 levelstats가 출력한 정수 MiB 정밀도입니다. 상세: structure_raw.tsv, levels_raw.tsv, levels_summary.tsv.',
              '', '| Workload | Metric (unit) | Baseline mean [min, max] | F2Load mean [min, max] | Mean change |',
              '|---|---|---:|---:|---:|']
    for r in summaries['behavior_summary']:
        change=(f"{r['percentage_point_difference']:+.4f} pp" if r['percentage_point_difference'] is not None
                else f"{r['mean_difference_pct']:+.3f}%" if r['mean_difference_pct'] is not None else 'N/A')
        lines.append(f"| {r['workload']} | {r['metric']} ({r['unit']}) | {fmt(r['baseline_mean'])} [{fmt(r['baseline_min'])}, {fmt(r['baseline_max'])}] | {fmt(r['f2load_mean'])} [{fmt(r['f2load_min'])}, {fmt(r['f2load_max'])}] | {change} |")
    lines += ['', '## 지표 정의와 해석 범위', '',
       '- filter_checks_per_get = (bloom.filter.useful + bloom.filter.full.positive) / number.keys.read. 분모는 RMW 내부 Get을 포함한 실제 Get 수입니다. 예전 paper_ch3_band25_raw.tsv의 filter_checks_per_lookup은 filter-cache accesses/전체 작업 수였으므로 혼용하지 않습니다.',
       '- positive_lookup_pct = successful_gets / number.keys.read × 100. Bloom positive 비율과 다릅니다. 전체 키 집합 recall도 아닙니다. E는 Get이 없어 조회당 지표를 비워 두었습니다.',
       '- bloom_false_positive_pct = (full.positive − full.true.positive) / (useful + full.positive − full.true.positive) × 100. 키가 없는 SST를 검사했을 때의 Bloom 오탐률입니다.',
       '- compaction_*_bytes는 YCSB 실행 중 엔진 compaction ticker입니다. per_op의 분모는 YCSB 작업 수입니다. 로딩 누적 쓰기량·WAF가 아닙니다. D/E의 컴팩션량은 매우 작아 상대 변화율만으로 차이를 해석하지 않습니다.',
       '- data_cache_misses_per_op는 전체 작업당 데이터 블록 캐시 miss이며 E의 scan, F의 RMW와 엔진 background 활동도 포함할 수 있습니다. 데이터 캐시 hit 비율은 hit/(hit+miss)입니다.',
       '- device 지표는 md0의 diskstats.end − diskstats.start이며 sector는 512 B입니다. Get 횟수 또는 엔진 SST bytes와 다릅니다. open/close 및 다른 host I/O가 포함된 프로세스 구간입니다.',
       '- avg_latency_us는 각 db_bench 실행의 평균 작업 지연시간입니다. operation_latency_raw.tsv는 작업 종류별 원본 평균/p50/p99를 보존하며 10개 histogram을 합친 percentile을 주장하지 않습니다.',
       '- 모든 120개 full cell에서 성공, 48 threads, 같은 workload별 옵션·측정 바이너리를 확인했습니다. f01-f05의 별도 clean settle과 f06-f10의 in-process waitforcompaction, Baseline byte copy와 F2Load fresh load의 준비 절차 차이는 남습니다.',
       '- 예전 loads.json의 일부 로딩/I/O 값은 stale template이므로 사용하지 않았습니다. 이 보고서는 그 파일로 10개 평균 로딩 시간/WAF를 계산하지 않습니다.',
       '- baseline 범위 안에 평균이 있다는 사실만으로 통계적 동등성이나 키 집합 일치를 입증하지 않습니다. 각 run의 세부 원본은 behavior_raw.tsv와 source_manifest.json에서 추적할 수 있습니다.',
       '', '## 키 집합 직접 비교: 별도 n=1', '']
    if keyset:
        lines += ['이 값은 baseline run3와 F2Load f06의 한 쌍이며 10개 평균이 아닙니다. 기존 스캔 bitmap의 popcount/교집합/합집합을 다시 계산했고 RESULTS.md §9.1과 일치했습니다.',
                  'Bitmap과 DB의 대응은 문서·파일명에 근거하며 최초 scanner 실행 명령은 찾지 못했습니다. 원본 경로·hash·계산은 keyset_f06_audit.json에 보존했습니다.',
                  '', '| 지표 | 값 |','|---|---:|']
        for k in ['baseline_unique_keys','f2load_unique_keys','intersection_keys','missing_keys',
                  'invented_keys','recall_pct','precision_pct','jaccard']:
            lines.append(f'| {k} | {fmt(keyset[k])} |')
    lines += ['', '## 재현', '', '```bash',
              'python3 experiments/analysis/export_pre_membership_fidelity.py \\',
              '  --output-dir experiments/results/20260914-072910_pre_membership_fidelity \\',
              '  --keyset-audit experiments/results/20260914-072910_pre_membership_fidelity/keyset_f06_audit.json',
              '```','']
    (out/'REPORT.md').write_text('\n'.join(lines))


def plot(out, records):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FuncFormatter
    panels = [('throughput_ops_sec','Throughput','ops/s'),
              ('avg_latency_us','Mean operation latency','us'),
              ('filter_checks_per_get','Filter checks per Get','checks/Get'),
              ('positive_lookup_pct','Successful Get requests','%'),
              ('compaction_write_bytes_per_op','Compaction writes per operation','B/op'),
              ('data_cache_misses_per_op','Data-block cache misses per operation','misses/op')]
    colors = {'baseline':'#2461a5','f2load':'#ce6b27'}
    fig, axes = plt.subplots(3,2,figsize=(12,11))
    for ax,(metric,title,unit) in zip(axes.flat,panels):
        for wi,w in enumerate('ABCDEF'):
            for system,offset in [('baseline',-.17),('f2load',.17)]:
                selected = sorted([r for r in records if r['system']==system and r['workload']==w
                                   and r[metric] is not None],key=lambda r:r['arm'])
                if not selected:
                    continue
                vals=[r[metric] for r in selected]
                xs=[wi+offset+(i-4.5)*.013 for i in range(10)]
                ax.vlines(wi+offset,min(vals),max(vals),color=colors[system],alpha=.6,lw=1)
                ax.scatter(xs,vals,s=17,color=colors[system],alpha=.65,zorder=3)
                ax.plot([wi+offset-.07,wi+offset+.07],[st.mean(vals)]*2,color=colors[system],lw=2.5)
                for x,r in zip(xs,selected):
                    if r['arm']=='f01':
                        ax.scatter([x],[r[metric]],s=42,facecolors='none',edgecolors='#111',lw=.8,zorder=4)
        ax.set_xticks(range(6),list('ABCDEF'))
        ax.set_title(title,loc='left',fontsize=12)
        ax.set_ylabel(unit)
        ax.set_ylim(bottom=0)
        if metric=='positive_lookup_pct':ax.set_ylim(0,100)
        ax.grid(axis='y',alpha=.18)
        ax.spines[['top','right']].set_visible(False)
        if unit in ['ops/s','B/op']:
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v,p: f'{v/1e6:g}M' if abs(v)>=1e6
                                        else f'{v/1e3:g}K' if abs(v)>=1e3 else f'{v:g}'))
    fig.suptitle('Before membership: RocksDB baseline 10 vs F2Load f01-f10',fontsize=16,y=.99)
    handles=[Line2D([],[],marker='o',color=colors[s],linestyle='',label=l)
             for s,l in [('baseline','Baseline (n=10)'),('f2load','F2Load (n=10)')]]
    handles.append(Line2D([],[],marker='o',markerfacecolor='none',color='#111',linestyle='',label='f01: recorded host interference'))
    fig.legend(handles=handles,loc='upper center',bbox_to_anchor=(.5,.962),ncol=3,frameon=False)
    fig.text(.06,.035,'1000 GiB input | 48 threads | 50 GiB cache | 300 s per workload. Each dot is one loaded DB.',fontsize=10)
    fig.text(.06,.015,'Horizontal marks: means. Vertical lines: min-max. E has no Get calls. C has zero compaction writes.',fontsize=10)
    fig.tight_layout(rect=(0,.06,1,.92),h_pad=2,w_pad=2)
    fig.savefig(out/'fidelity.png',dpi=170)
    fig.savefig(out/'fidelity.pdf')
    plt.close(fig)


if __name__ == '__main__':
    main()
