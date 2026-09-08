#!/usr/bin/env python3
"""Validate a completed campaign and replace shared paper data and prose."""
import argparse
import csv
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from ch23_common import (CLEAN, CONFIGS, EXPERIMENTS, HASHES, SYSTEMS,
    WORKSPACE, bench_stats, db_identity, read_metrics, require, save_json, sha, ticker)
from paper_alternative_tikz import write_source
from ch23_paper_update import update_paper
from ch23_f2_paper_update import update_f2_paper


def write_tsv(path, rows):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with Path(path).open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields,delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)


def disk_bytes(raw, direction, end='after_sync'):
    def count(path):
        for line in path.read_text().splitlines():
            parts=line.split()
            if parts[2]=='md0':
                if direction=='read_ios': return int(parts[3])
                return int(parts[5 if direction=='read' else 9])*512
        raise RuntimeError('missing md0 disk counter')
    return count(raw/('diskstats.'+end))-count(raw/'diskstats.start')


def extra_load_metrics(row):
    comp_read=comp_count=comp_us=stall_us=device_write=0
    for phase in row['phases']:
        log=Path(phase['log_dir'])
        text=(log/'bench.out').read_text()
        comp_read+=ticker(text,'rocksdb.compact.read.bytes')
        stall_us+=ticker(text,'rocksdb.stall.micros')
        m=re.findall(r'^rocksdb.compaction.times.micros .*?COUNT\s*:\s*(\d+)\s+SUM\s*:\s*(\d+)',text,re.M)
        require(m,'missing compaction histogram')
        comp_count+=int(m[-1][0]); comp_us+=int(m[-1][1])
        device_write+=disk_bytes(log/'raw','write')
    row.update(compaction_read_bytes=comp_read, compaction_jobs=comp_count,
               cumulative_compaction_sec=comp_us/1e6, stall_sec=stall_us/1e6,
               device_write_bytes=device_write, device_waf=device_write/row['logical_input_bytes'])
    if row['system']=='baseline' and row['key_bytes']==24:
        created=set()
        for path in sorted(Path(row['db_dir']).glob('LOG*')):
            if not path.is_file(): continue
            with path.open(errors='replace') as f:
                for line in f:
                    if '"event": "table_file_creation"' in line:
                        match=re.search(r'"file_number":\s*(\d+)',line)
                        if match: created.add(int(match.group(1)))
        require(created,'missing SST creation evidence')
        row['created_sst_count']=len(created)
        row['retained_sst_pct']=100*row['final_sst_count']/len(created)


def figure2(directory, loads):
    # Keep the historical series intact. Overlay distinct, unconnected common-
    # baseline time markers; never splice one new point into an old series.
    scale_path=directory/'bg_loading_scale.tex'
    old=scale_path.read_text()
    active=old.split('% Active vertical figure generated from the promoted TSV data.\n')[-1]
    require('1.30/0.5/' in active,'unexpected historical scaling figure')
    active=active.replace('DB size (TiB)','Logical input (1,000 GiB)')
    active=active.replace('% x / DB size (TiB)', '% x / logical input (1,000 GiB)')
    marker_comment='% Common release / 16-buffer 1,000-GiB measurements, unconnected diamonds.'
    if marker_comment in active:
        active=active[:active.index(marker_comment)].rstrip()+'\n'+r'\end{tikzpicture}'
    marker_lines=['% Common release / 16-buffer 1,000-GiB measurements, unconnected diamonds.']
    for x,case in ((2.11,'baseline_1kb'),(2.29,'baseline_91b')):
        hour=loads[case]['elapsed_sec']/3600
        y=.95+3.2*math.log(hour/.5)/math.log(200)
        marker_lines += [r'\draw[draw=black,fill=white,line width=0.8pt] '
            r'(%.4f,%.4f)--(%.4f,%.4f)--(%.4f,%.4f)--(%.4f,%.4f)--cycle;' %
            (x,y+.095,x+.08,y,x,y-.095,x-.08,y)]
    marker_lines += [
        r'\draw[draw=black,fill=white,line width=0.8pt] '
        r'(3.15,4.56)--(3.23,4.46)--(3.15,4.36)--(3.07,4.46)--cycle;',
        r'\node[anchor=west,font=\normalsize] at (3.33,4.46) {Common};']
    active=active.replace(r'\end{tikzpicture}', '\n'.join(marker_lines)+'\n'+r'\end{tikzpicture}')
    write_source(directory,'bg_loading_scale',active.splitlines())
    maximum=max(loads[k]['elapsed_sec']/3600 for k in ('baseline_1kb','baseline_91b','flush_only_1kb','flush_only_91b'))
    upper=max(5,math.ceil(maximum*1.22))
    y=lambda h: .95+3.2*h/upper
    lines=[r'\definecolor{figTwoFlush}{HTML}{D55E00}',
           r'\definecolor{figTwoConventional}{HTML}{4D4D4D}',
           r'\begin{tikzpicture}[x=1cm,y=1cm,font=\normalsize,',
           r'axis/.style={draw=black!75,line width=0.45pt},',
           r'flushOnly/.style={draw=black!75,fill=figTwoFlush,line width=0.45pt,',
           r'    postaction={pattern=north east lines,pattern color=black!45}},',
           r'conventional/.style={draw=black!75,fill=figTwoConventional,line width=0.45pt}]',
           r'\path[use as bounding box] (-0.30,-0.05) rectangle (5.45,4.80);',
           r'\draw[axis] (0.75,0.95)--(5.35,0.95);',
           r'\draw[axis] (0.75,0.95)--(0.75,4.15);']
    for tick in range(upper+1):
        lines += [r'\draw[black!12] (0.75,%.5f)--(5.35,%.5f);'%(y(tick),y(tick)),
                  r'\node[anchor=east] at (0.64,%.5f) {%d};'%(y(tick),tick)]
    for center,kv,label in ((2.05,'1kb','1KB'),(4.05,'91b','91B')):
        for offset,mode,style in ((-.25,'flush_only','flushOnly'),(.25,'baseline','conventional')):
            row=loads[mode+'_'+kv]; x=center+offset; height=y(row['elapsed_sec']/3600)
            lines += ['%% %s: %.9f s, WAF %.9f, source %s'%(row['case_id'],row['elapsed_sec'],row['waf'],row['log_dir']),
                r'\draw[%s] (%.4f,0.95) rectangle (%.4f,%.5f);'%(style,x-.25,x+.25,height),
                r'\node[anchor=west,rotate=90] at (%.4f,%.5f) {%.2f$\times$};'%(x,height+.07,row['waf'])]
        lines += [r'\node[anchor=north] at (%.4f,0.88) {%s};'%(center,label)]
    lines += [r'\node[rotate=90,anchor=south,font=\Large] at (-0.02,2.55) {Loading time (hours)};',
              r'\node[anchor=north] at (3.05,0.43) {KV size (1,000 GiB)};',
              r'\draw[flushOnly] (0.90,4.34) rectangle (1.22,4.58);',
              r'\node[anchor=west] at (1.32,4.46) {Flush-only};',
              r'\draw[conventional] (3.15,4.34) rectangle (3.47,4.58);',
              r'\node[anchor=west] at (3.57,4.46) {Conventional};',r'\end{tikzpicture}']
    write_source(directory,'bg_loading_flush_only',lines)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-root',required=True,type=Path)
    p.add_argument('--paper-root',type=Path,default=WORKSPACE/'paper')
    p.add_argument('--exclude-f2load',action='store_true',
                   help='Promote the explicitly completed 7-load/20-read scope; omit F2Load.')
    p.add_argument('--validate-only',action='store_true')
    p.add_argument('--f2-completion-update',action='store_true',
                   help='Add the completed F2Load experiment to the current edited manuscript.')
    args=p.parse_args(); root=args.run_root.resolve(); paper=args.paper_root.resolve()
    marker='NON_F2_COMPLETED.json' if args.exclude_f2load else 'COMPLETED'
    require((root/marker).is_file(),'requested campaign scope incomplete; cannot promote')
    systems=tuple(s for s in SYSTEMS if not (args.exclude_f2load and s=='f2load'))
    require(not (root/'PROMOTED.json').exists(),'campaign already promoted')
    manifest=json.loads((root/'manifest.json').read_text())
    require(manifest['phase']=='full' and manifest['dataset_gib']==1000,'pilots cannot become paper results')
    loads=json.loads((root/'loads.json').read_text()); reads=json.loads((root/'reads.json').read_text())
    require(set(loads)=={s+'_1kb' for s in systems}|{'baseline_91b','flush_only_91b'},'load matrix mismatch')
    require(set(reads)=={c+'/'+s for c in CONFIGS for s in systems},'read matrix mismatch')
    for case,row in loads.items():
        require(row['status']=='validated','unvalidated load')
        saved=json.loads(Path(row['source_identity_file']).read_text())
        require(db_identity(row['db_dir'])==saved,'source DB changed: '+case)
        extra_load_metrics(row)
    if args.f2_completion_update:
        require(not args.exclude_f2load, 'F2Load cannot be included and excluded together')
        f2=loads['f2load_1kb']
        require(f2['load_protocol']=='materialize_then_clean_automatic_settle','unexpected F2Load protocol')
        require(f2['pending_after_settle']==0,'unsettled F2Load result')
        require(f2['elapsed_sec']==sum(p['elapsed_sec'] for p in f2['phases']), 'F2Load tail time excluded')
        require(f2['completion_binary_sha256']==HASHES[str(CLEAN)], 'F2Load completion binary mismatch')
        previous=Path(manifest['reused_campaign'])
        require(sha(previous/'loads.json')==manifest['reused_loads_sha256'], 'reused load file changed')
        require(sha(previous/'reads.json')==manifest['reused_reads_sha256'], 'reused read file changed')
        original_loads=json.loads((previous/'loads.json').read_text())
        original_reads=json.loads((previous/'reads.json').read_text())
        for case,row in original_loads.items():
            require(all(loads[case][k]==v for k,v in row.items()), 'prior control load changed: '+case)
        for rid,row in original_reads.items():
            require(reads[rid]==row, 'prior control read changed: '+rid)
    last=loads['last_comp_1kb']; prefix=loads['flush_only_1kb']
    last['device_write_bytes']+=prefix['device_write_bytes']
    last['device_waf']=last['device_write_bytes']/last['logical_input_bytes']
    last['stall_sec']+=prefix['stall_sec']
    last['peak_rss_kb']=max(last['peak_rss_kb'],prefix['peak_rss_kb'])
    for rid,row in reads.items():
        log=Path(row['result_dir']); cmd=json.loads((log/'raw'/'command.json').read_text())
        require(cmd[0]==str(CLEAN) and row['binary_sha256']==HASHES[str(CLEAN)],'reader binary mismatch')
        for flag in ('--duration=300','--threads=48','--readonly=true','--merge_operator=put',
                     '--read_random_exp_range=0','--seed=87654321','--ops_between_duration_checks=1'):
            require(flag in cmd,'reader option mismatch: '+flag)
        observed=read_metrics((log/'bench.out').read_text(),1000)
        for metric in observed:
            require(observed[metric]==row[metric],'read raw/summary mismatch: '+rid+'/'+metric)
        require(row['source_db_dir']==loads[row['system']+'_1kb']['db_dir'],'Figure 4/5 DB mismatch')
        base=reads[row['config_id']+'/baseline']
        row['throughput_vs_baseline']=row['throughput_ops_sec']/base['throughput_ops_sec']
        row['latency_vs_baseline']=row['avg_latency_us']/base['avg_latency_us']
        row['filter_probes_vs_baseline']=row['filter_probes_per_op']/base['filter_probes_per_op']
        row['disk_read_ios']=disk_bytes(log/'raw','read_ios','end')
        row['disk_read_mib']=disk_bytes(log/'raw','read','end')/1024**2
    if args.validate_only:
        print(json.dumps(dict(validated_loads=len(loads),validated_reads=len(reads),
            f2load_excluded=args.exclude_f2load,
            loading_seconds={k:v['elapsed_sec'] for k,v in loads.items()},
            sst_waf={k:v['waf'] for k,v in loads.items()},
            structural_metrics={s:{k:reads['D_pinned_5pct/'+s][k] for k in
                ('filter_probes_per_op','filter_positive_pct','successful_lookup_pct')}
                for s in systems}),indent=2))
        return
    backup=root/'promotion_backup'; backup.mkdir()
    # Protect edits made by the author while staging/building the new paper.
    protected=[paper/'main.tex']+list((paper/'tex').glob('*.tex'))+list((paper/'figs').glob('*.tex'))
    original_hashes={str(p):sha(p) for p in protected}
    shutil.copy2(str(paper/'main.tex'),str(backup/'main.tex'))
    if (paper/'main.pdf').exists():
        shutil.copy2(str(paper/'main.pdf'),str(backup/'main.pdf'))
    shutil.copytree(str(paper/'tex'),str(backup/'tex'))
    shutil.copytree(str(paper/'figs'),str(backup/'figs'))
    result_dir=EXPERIMENTS/'results'
    old_loading=result_dir/'paper_figure4_loading_time_1tb_single.tsv'
    old_reads=result_dir/'paper_figure4_uniform_read_cache_5m_single.tsv'
    old_fig2=result_dir/'paper_figure2_loading_waf.tsv'
    for f in (old_loading,old_reads,old_fig2): shutil.copy2(str(f),str(backup/f.name))
    with old_loading.open() as f: historical=list(csv.DictReader(f,delimiter='\t'))
    by_method={r['method']:r for r in historical}
    for name,seconds in (('ADOC',4122),('BlobDB (GC off)',1712)):
        require(float(by_method[name]['elapsed_sec'])==seconds,'approved reuse value drift: '+name)
        require((EXPERIMENTS.parent/by_method[name]['source']).is_file(),'reuse evidence missing: '+name)
    names=dict(baseline='Baseline',flush_only='Flush only',last_comp='Last compaction',
               fillseq='Fillseq',fillseq_ow='Fillseq + 10% overwrite',f2load='F2Load')
    if args.exclude_f2load:
        names.pop('f2load')
        by_method.pop('F2Load',None)
    for system,name in names.items():
        row=loads[system+'_1kb']
        by_method[name]=dict(method=name,dataset_gib=1000,key_bytes=24,value_bytes=1000,
             kv_label='1KB',elapsed_sec=row['elapsed_sec'],loading_min=row['loading_min'],repetitions=1,
             validation=row['validation'],status='validated_common_campaign',source=row['log_dir']+'/validated.json',
             db_dir=row['db_dir'],binary_sha256=row['binary_sha256'])
        if system=='f2load' and args.f2_completion_update:
            for key in ('load_protocol','materialized_process_sec','settle_process_sec',
                        'completion_binary_sha256','pending_before_settle','pending_after_settle'):
                by_method[name][key]=row[key]
    loading_rows=[by_method[name] for name in ('Baseline','ADOC','BlobDB (GC off)','Flush only',
                  'Last compaction','Fillseq','Fillseq + 10% overwrite','F2Load')
                  if name!='F2Load' or not args.exclude_f2load]
    read_rows=[reads[c+'/'+s] for c in CONFIGS for s in systems]
    fig2_rows=[]
    for kv,label in (('1kb','1KB'),('91b','91B')):
        for mode,title in (('flush_only','Flush-only'),('baseline','Conventional')):
            row=loads[mode+'_'+kv]
            fig2_rows.append(dict(kv_size=label,mode=title,target_gib=1000,repetitions=1,
                elapsed_sec=row['elapsed_sec'],elapsed_hour=row['elapsed_sec']/3600,
                logical_input_bytes=row['logical_input_bytes'],flush_sst_write_bytes=row['flush_sst_write_bytes'],
                compaction_sst_write_bytes=row['compaction_sst_write_bytes'],total_sst_write_bytes=row['total_sst_write_bytes'],
                waf=row['waf'],source=row['log_dir'],binary_sha256=row['binary_sha256']))
    bundle=result_dir/manifest['run_id']; bundle.mkdir(exist_ok=False)
    save_json(bundle/'loads.json',loads); save_json(bundle/'reads.json',reads)
    save_json(bundle/'manifest.json',manifest)
    if args.f2_completion_update:
        save_json(bundle/'f2_completion.json',dict(
            protocol=f2['load_protocol'], elapsed_sec=f2['elapsed_sec'],
            materialized_process_sec=f2['materialized_process_sec'],settle_process_sec=f2['settle_process_sec'],
            pending_before_settle=f2['pending_before_settle'],pending_after_settle=f2['pending_after_settle'],
            settle_compaction_read_bytes=f2['settle_compaction_read_bytes'],
            settle_compaction_write_bytes=f2['settle_compaction_write_bytes'],
            device_write_bytes=f2['device_write_bytes'],device_waf=f2['device_waf'],
            phases=f2['phases'],final_db_dir=f2['db_dir'],materialized_db_dir=f2['pre_settle_db_dir'],
            reused_load_states=len(original_loads),reused_read_cells=len(original_reads),
            limitations=['One repetition per cell',manifest['timing_limit'],
                        'F2Load format 6; conventional loaders format 7',
                        'Approximate key membership; no claim of controlled layout-only speedup',
                        'Checkpoint preparation and between-phase cache reset excluded from both measured process times']))
    sources=bundle/'analysis_sources'; sources.mkdir()
    for source in sorted((EXPERIMENTS/'analysis').glob('*ch23*.py'))+[
            EXPERIMENTS/'analysis'/'paper_alternative_tikz.py',
            EXPERIMENTS/'analysis'/'plot_paper_figure4_uniform_read_cache.py']:
        shutil.copy2(str(source),str(sources/source.name))
    save_json(sources/'sha256.json',{p.name:sha(p) for p in sorted(sources.glob('*.py'))})
    save_json(bundle/'scope.json',dict(validated_loads=len(loads),validated_reads=len(reads),
        f2load_excluded=args.exclude_f2load,completion_marker=str(root/marker),
        reused_methods=['ADOC','BlobDB (GC off)'],full_campaign_complete=not args.exclude_f2load))
    for path,rows in ((old_loading,loading_rows),(old_reads,read_rows),(old_fig2,fig2_rows)):
        write_tsv(bundle/path.name,rows)
    # Reviewable staging first; only a successfully built paper is installed.
    stage=root/'paper_staging'; shutil.copytree(str(paper),str(stage))
    if args.f2_completion_update:
        # The previously promoted shared baseline/diamonds remain identical.
        update_f2_paper(stage,loads,reads,manifest,loading_rows)
    else:
        figure2(stage/'figs',loads)
        update_paper(stage,loads,reads,manifest,loading_rows,exclude_f2load=args.exclude_f2load)
    with (root/'plot.log').open('w') as out:
        subprocess.run([sys.executable,str(EXPERIMENTS/'analysis'/'plot_paper_figure4_uniform_read_cache.py'),
            '--loading-tsv',str(bundle/old_loading.name),'--read-tsv',str(bundle/old_reads.name),
            '--output-dir',str(stage/'figs')]+(['--exclude-f2load'] if args.exclude_f2load else []),
            check=True,stdout=out,stderr=subprocess.STDOUT)
    subprocess.run([sys.executable,str(EXPERIMENTS/'analysis'/'export_paper_ch23_tikz.py'),
        '--figure-dir',str(stage/'figs'),'--build-dir',str(root/'tikz_exports')],check=True)
    with (root/'paper_build.log').open('w') as out:
        subprocess.run(['latexmk','-pdf','-interaction=nonstopmode','-halt-on-error','main.tex'],
                       cwd=str(stage),check=True,stdout=out,stderr=subprocess.STDOUT)
    build=(stage/'main.log').read_text(errors='replace')
    require('There were undefined references' not in build,'unresolved references after promotion')
    # Export every final page for the subsequent visual audit; these artifacts
    # are previews, not a claim that a human/agent has inspected the pages.
    previews=root/'paper_page_previews'; previews.mkdir()
    subprocess.run(['gs','-q','-dSAFER','-dBATCH','-dNOPAUSE','-sDEVICE=png16m',
        '-r110','-sOutputFile='+str(previews/'page-%02d.png'),str(stage/'main.pdf')],check=True)
    require(all(Path(p).is_file() and sha(p)==digest for p,digest in original_hashes.items()),
            'author edited manuscript during staging; preserve both copies and refresh before installation')
    for path,rows in ((old_loading,loading_rows),(old_reads,read_rows),(old_fig2,fig2_rows)):
        shutil.copy2(str(bundle/path.name),str(path))
    for folder in ('tex','figs'):
        for f in (stage/folder).iterdir():
            dest=paper/folder/f.name
            if f.is_file() and (not dest.exists() or sha(f)!=sha(dest)):
                shutil.copy2(str(f),str(dest))
    shutil.copy2(str(stage/'main.tex'),str(paper/'main.tex'))
    shutil.copy2(str(stage/'main.pdf'),str(paper/'main.pdf'))
    preview_dir=result_dir/'paper_figure4_uniform_read_cache_figures'
    for f in (stage/'figs').glob('bg_alternative_*'):
        if f.suffix in ('.pdf','.png'): shutil.copy2(str(f),str(preview_dir/f.name))
    save_json(root/'PROMOTED.json',dict(bundle=str(bundle),paper=str(paper/'main.pdf'),
        shared_baseline_sec=loads['baseline_1kb']['elapsed_sec'],read_cells=len(reads),
        validated_loads=len(loads),full_campaign_complete=not args.exclude_f2load,
        f2load_excluded=args.exclude_f2load,
        deferred='Historical scaling and breakdown retained and labeled separately; '+
                 ('F2Load omitted from common Figures 4/5 pending validation.' if args.exclude_f2load else '')))
    report = ('# Common Chapter 2/3 campaign results\n\n'
        'Run: `{}`. {} load states and {} read cells validated.\n\n'.format(manifest['run_id'],len(loads),len(reads))+
        ('F2Load remains deferred and is omitted from the new common comparison; its old results are not substituted.\n\n'
            if args.exclude_f2load else '')+
        'The Figure 5 DBs are the exact sources of the corresponding new Figure 4 loading bars. '
        'All readers and conventional load alternatives use clean release SHA-256 `{}`. '
        'Readers use uniform readrandom and the common generic read-only path.\n\n'.format(HASHES[str(CLEAN)])+
        '| Method | Seconds | Source |\n| --- | ---: | --- |\n'+
        ''.join('| {} | {:.3f} | `{}` |\n'.format(r['method'],float(r['elapsed_sec']),r['source']) for r in loading_rows)+
        '\nFigure 2(a) historical bars/lines and Figure 2(c) historical instrumentation remain explicitly '
        'separate. Figure 2(a) overlays the new 1,000-GiB baseline times as unconnected diamonds; '
        'Figure 2(b) shares its two 1-KB cells with Figure 4.\n\n'
        'Generated prose numbers: `paper/tex/ch23_measurements.tex`. Raw runs are preserved under '
        '`{}`. Promotion backup and build log are in that directory.\n'.format(root))
    if args.f2_completion_update:
        report += ('\n## F2Load completion protocol\n\n'
            'Only F2Load was newly measured in this extension; the previous seven loads and twenty reads '
            'are reused byte-for-byte from their validated summaries. The four new F2Load reads use its '
            'new completed source DB. The combined campaign contains eight load states and twenty-four read cells.\n\n'
            'F2Load took {:.6f} s for initial loading/materialization plus {:.6f} s for reopening with '
            'the common clean binary and automatic compaction, for {:.6f} s total ({:.2f}x baseline speedup). '
            'Estimated pending bytes fell from {:,} to zero. The completion phase read {:.3f} GiB and '
            'wrote {:.3f} GiB of actual SST data. Both phases are included in loading time and device I/O; '
            'checkpoint preparation and the between-phase cache reset are excluded. No new keys are inserted '
            'during completion. The first materialized DB is preserved separately.\n\n'
            'The frozen F2Load SHA-256 is `{}`; completion uses the same clean SHA above. F2Load retains '
            'format 6, whereas conventional loaders use format 7. F2Load was measured later than the '
            'controls and all cells have one repetition. Its approximate key membership is reported in '
            'Figure 5(b); throughput ratios do not isolate layout effects from key-set differences.\n\n'
            '| Read configuration | F2Load ops/s | / Baseline | Successful lookups (%) |\n'
            '| --- | ---: | ---: | ---: |\n').format(
                f2['materialized_process_sec'],f2['settle_process_sec'],f2['elapsed_sec'],
                loads['baseline_1kb']['elapsed_sec']/f2['elapsed_sec'],f2['pending_before_settle'],
                f2['settle_compaction_read_bytes']/1024**3,f2['settle_compaction_write_bytes']/1024**3,
                f2['binary_sha256'])
        report += ''.join('| {} | {:.0f} | {:.3f} | {:.3f} |\n'.format(c,
            reads[c+'/f2load']['throughput_ops_sec'],reads[c+'/f2load']['throughput_vs_baseline'],
            reads[c+'/f2load']['successful_lookup_pct']) for c in CONFIGS)
        report += '\nDetailed machine-readable protocol: `f2_completion.json` in the result bundle. Analysis sources and hashes are archived alongside it.\n'
    (EXPERIMENTS/'docs'/'PAPER_CHAPTER23_COMMON_RESULTS.md').write_text(report)
    (bundle/'RESULTS.md').write_text(report)
    notice=('**Current validated campaign:** `{}`. Numerical entries and source-DB mappings '
        'below from earlier runs are historical. Use '
        '[PAPER_CHAPTER23_COMMON_RESULTS.md](PAPER_CHAPTER23_COMMON_RESULTS.md) and the current '
        'promoted TSVs for Figures 2(b), 4 and 5. All {} included Figure 5 cells were rerun with '
        'the common clean release readrandom driver; historical YCSB values are not mixed. '
        '{}\n\n').format(manifest['run_id'],len(reads),
            'F2Load is deferred and omitted from the current common comparison.' if args.exclude_f2load else '')
    for name in ('PAPER_CHAPTER23_COMMON_BASELINE.md','PAPER_FIGURE4_FIXED_CONFIGURATION.md',
                 'PAPER_FIGURE4_UNIFORM_READ_CACHE_MATRIX.md','PAPER_FIGURE4_DB_INVENTORY.md',
                 'PAPER_FIGURE2_FLUSH_COMPACTION.md'):
        path=EXPERIMENTS/'docs'/name
        original=path.read_text()
        first,rest=original.split('\n',1)
        path.write_text(first+'\n\n'+notice+rest.lstrip('\n'))
    print('PROMOTED',bundle,flush=True)


if __name__=='__main__': main()
