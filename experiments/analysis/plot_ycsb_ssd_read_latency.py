#!/usr/bin/env python3
"""Add physical SSD mean read latency to a frozen YCSB fidelity snapshot."""
import argparse
import base64
import csv
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from compare_five_baseline_ycsb import EXP, RESULTS, read_cells, tsv
from plot_ycsb_raw_metrics import style

COUNTER_REFERENCE = 'https://www.kernel.org/doc/html/latest/admin-guide/iostats.html'


def disk_counters(path):
    out = {}
    for line in path.read_text().splitlines():
        fields = line.split()
        assert len(fields) >= 7, path
        out[fields[2]] = dict(read_ios=int(fields[3]), read_time_ms=int(fields[6]))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,default=EXP/'artifacts/analysis/ycsb_repeat_fidelity_260912_f10_dots')
    parser.add_argument('--output-dir',type=Path,default=EXP/'artifacts/analysis/ycsb_ssd_read_latency_260912')
    args = parser.parse_args()
    source, out = args.snapshot.resolve(), args.output_dir.resolve()
    manifest = json.loads((source/'provenance.json').read_text())
    prior = list(csv.DictReader((source/'raw_metrics.tsv').open(),delimiter='\t'))
    selected = manifest['selected']
    by_series = {s['label']:s for s in selected}
    labels = list(by_series)
    assert len(by_series) == len(selected)
    assert len(prior) == manifest['validated_cells'] == len(labels)*6
    assert len({(r['series'],r['workload']) for r in prior}) == len(prior)
    files = {source/'raw_metrics.tsv',source/'provenance.json',Path(__file__).resolve()}
    campaigns, members_by_run = {}, {}
    per_device, latency_rows, augmented = [], [], []
    for row in prior:
        run,system,w,label = row['source_run'],row['system'],row['workload'],row['series']
        assert by_series[label]['run_id'] == run and by_series[label]['system'] == system
        if run not in campaigns:
            campaigns[run] = read_cells(run,system)
            env_path = RESULTS/run/'provenance/environment.txt'
            env = env_path.read_text()
            raid_line = next(line for line in env.splitlines() if re.match(r'^md0\s*:',line))
            members = re.findall(r'\b(nvme\d+n\d+)\[\d+\]',raid_line)
            assert len(members) == len(set(members)) == 31, (run,members)
            assert set(members) == {'nvme%dn1'%n for n in range(1,32)}, (run,members)
            members_by_run[run] = sorted(members,key=lambda d:int(re.search(r'\d+',d).group()))
            files.update([env_path,RESULTS/run/'results.json'])
        cell = campaigns[run][w.lower()]
        assert cell['binary_sha256'] == row['binary_sha256']
        assert float(row['started_epoch']) == cell['started_epoch']
        assert float(row['ended_epoch']) == cell['ended_epoch']
        raw = Path(cell['log_dir'])/'raw'
        paths = [raw/'diskstats.start',raw/'diskstats.end']
        start,end = [disk_counters(p) for p in paths]
        files.update(paths)
        assert end['md0']['read_ios']-start['md0']['read_ios'] == int(row['device_read_ios'])
        assert start['md0']['read_time_ms'] == end['md0']['read_time_ms'] == 0
        total_reads, total_ms = 0,0
        for device in members_by_run[run]:
            reads = end[device]['read_ios']-start[device]['read_ios']
            read_ms = end[device]['read_time_ms']-start[device]['read_time_ms']
            assert reads > 0 and read_ms > 0, (label,w,device,reads,read_ms)
            total_reads += reads
            total_ms += read_ms
            per_device.append(dict(series=label,system=system,workload=w,device=device,
                                   ssd_read_ios=reads,ssd_read_time_ms=read_ms,
                                   ssd_read_latency_us=1000*read_ms/reads,source_run=run))
        added = dict(ssd_read_latency_us=1000*total_ms/total_reads,ssd_read_ios=total_reads,
                     ssd_read_time_ms=total_ms,ssd_device_count=len(members_by_run[run]),
                     ssd_devices=','.join(members_by_run[run]))
        latency_rows.append(dict(series=label,system=system,workload=w,**added,source_run=run,
                                 started_epoch=row['started_epoch'],ended_epoch=row['ended_epoch']))
        augmented.append(dict(row,**added))
    assert len(per_device) == len(prior)*31
    assert all({k:now[k] for k in before} == before for before,now in zip(prior,augmented))
    out.mkdir(parents=True,exist_ok=True)
    tsv(out/'ssd_read_latency.tsv',latency_rows)
    tsv(out/'raw_metrics_with_ssd_latency.tsv',augmented)
    tsv(out/'per_ssd_read_latency.tsv',per_device)
    summary = []
    for w in 'ABCDEF':
        line = dict(workload=w,unit='us')
        for system in ['baseline','f2load']:
            values = [r['ssd_read_latency_us'] for r in latency_rows if r['system']==system and r['workload']==w]
            line.update({system+'_mean_us':statistics.mean(values),system+'_min_us':min(values),
                         system+'_max_us':max(values),system+'_runs':len(values)})
        summary.append(line)
    tsv(out/'summary.tsv',summary)

    base = [s['label'] for s in selected if s['system']=='baseline']
    f2 = [s['label'] for s in selected if s['system']=='f2load']
    colors = {s:plt.cm.Blues(0.88-0.53*i/max(1,len(base)-1)) for i,s in enumerate(base)}
    colors.update({s:plt.cm.Oranges(0.88-0.40*i/max(1,len(f2)-1)) for i,s in enumerate(f2)})
    values = {(r['series'],r['workload']):r['ssd_read_latency_us'] for r in latency_rows}
    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':14,'axes.labelsize':16,'pdf.fonttype':42})
    fig,ax = plt.subplots(figsize=(17,6.5))
    fig.subplots_adjust(left=0.075,right=0.985,bottom=0.23,top=0.76)
    step = 0.86/len(labels)
    for i,label in enumerate(labels):
        xs = [x-0.43+(i+0.5)*step for x in range(6)]
        ax.scatter(xs,[values[(label,w)] for w in 'ABCDEF'],s=40,color=colors[label],
                   edgecolors='#324d63',linewidths=0.35,zorder=3)
    style(ax)
    ax.set_xticks(range(6),list('ABCDEF'))
    ax.tick_params(labelsize=14)
    ax.set_xlim(-0.7,5.7)
    ax.set_ylim(0,max(values.values())*1.15)
    ax.set_ylabel('Mean read latency (µs)')
    ax.set_xlabel('YCSB workload',labelpad=10)
    fig.suptitle('SSD mean read latency | %d baseline loads, %d F2Load states'%(len(base),len(f2)),fontsize=23,y=0.985)
    fig.legend(handles=[Line2D([],[],linestyle='none',marker='o',markersize=7,color=colors[s],label=s) for s in labels],
               ncol=max(7,(len(labels)+1)//2),loc='upper center',bbox_to_anchor=(0.5,0.925),
               frameon=False,fontsize=14,columnspacing=1.35,handlelength=1.35)
    fig.text(0.075,0.080,'Each point is one run. Read-count-weighted mean over the 31 NVMe SSDs in md0; OS drive excluded.',fontsize=12,color='#52697a')
    fig.text(0.075,0.035,'Kernel queue + completion time; full process window includes DB open/close and background device I/O.',fontsize=12,color='#52697a')
    assert ax.get_ylim()[0] == 0 and ax.get_yscale() == 'linear'
    assert sum(len(c.get_offsets()) for c in ax.collections) == len(prior)
    fig.savefig(out/'ssd_read_latency.png',dpi=180)
    fig.savefig(out/'ssd_read_latency.pdf')
    plt.close(fig)

    definition = ('1000 * sum(delta read milliseconds) / sum(delta completed reads), across historical md0 member SSDs. '
                  'Whole diskstats line uses zero-based indexes 6 and 3, respectively. '
                  'This is a read-count-weighted mean of block-device read completion latency (r_await equivalent), '
                  'including kernel queueing; it is not a RocksDB Get/SST timer or NAND-only service time. '
                  'md0 timing counters are unpopulated (zero) and are not used. The OS drive nvme0n1 is excluded. '
                  'The start/end process window includes DB open/close, background compaction, and other device activity. '
                  'Only the mean is plotted; no percentile is inferred from these aggregate counters.')
    provenance = dict(created_utc=datetime.now(timezone.utc).isoformat(),snapshot=str(source),
                      selected=selected,omitted=manifest['omitted'],observations=len(prior),
                      per_device_observations=len(per_device),definition=definition,
                      counter_reference=COUNTER_REFERENCE,members_by_run=members_by_run,
                      inherited_protocol=manifest['protocol'],
                      source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)},
                      reproduce_argv=['python3','experiments/analysis/plot_ycsb_ssd_read_latency.py',
                                      '--snapshot',str(source),'--output-dir',str(out)])
    (out/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
    (out/'README.md').write_text('# SSD mean read latency\n\n'+definition+'\n\n'+
        'Inputs: '+str(source)+' (same %d cells).\n\n'%len(prior)+
        'raw_metrics_with_ssd_latency.tsv preserves all previous columns and adds SSD mean read latency, '
        'completed read count, accumulated read milliseconds, device count, and names. '
        'ssd_read_latency.tsv is the compact table; per_ssd_read_latency.tsv retains each device.\n\n'+
        manifest['protocol']+'\n\nCounter definition: '+COUNTER_REFERENCE+'\n')
    encoded = base64.b64encode((out/'ssd_read_latency.png').read_bytes()).decode()
    section = '<h2>SSD mean read latency</h2><p>'+html.escape(definition)+'</p><img alt="SSD mean read latency" src="data:image/png;base64,'+encoded+'">'
    section += '<p><a href="raw_metrics_with_ssd_latency.tsv">Full TSV including SSD latency</a></p>'
    section += '<table><tr><th>Workload</th><th>Baseline mean (µs)</th><th>Baseline min–max</th><th>F2Load mean (µs)</th><th>F2Load min–max</th></tr>'
    for r in summary:
        cells=[r['workload'],'%.2f'%r['baseline_mean_us'],'%.2f–%.2f'%(r['baseline_min_us'],r['baseline_max_us']),
               '%.2f'%r['f2load_mean_us'],'%.2f–%.2f'%(r['f2load_min_us'],r['f2load_max_us'])]
        section += '<tr>'+''.join('<td>'+c+'</td>' for c in cells)+'</tr>'
    section += '</table>'
    old_report = (source/'report.html').read_text()
    marker = '<h2>Numerical summaries'
    assert marker in old_report
    (out/'report.html').write_text(old_report.replace(marker,section+marker,1))
    print('Output:',out)
    print('Validated %d runs × 31 SSDs; %d latency points; original TSV columns preserved.'%(len(prior),len(values)))
    for r in summary:
        print(r['workload'],'baseline %.2f µs; F2Load %.2f µs'%(r['baseline_mean_us'],r['f2load_mean_us']))


if __name__ == '__main__':
    main()
