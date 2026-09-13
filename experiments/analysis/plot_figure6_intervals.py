#!/usr/bin/env python3
"""Actual-unit preview of Figure 6, using the same ten validated baseline loads."""
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics as st

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D

from compare_five_baseline_ycsb import EXP, tsv
from plot_ch3_band import ARMS

SNAPSHOT = EXP / 'artifacts/analysis/ycsb_repeat_fidelity_260911_f05'
OUT = EXP / 'artifacts/analysis/figure6_intervals_260911'
GIB = 1024 ** 3
BLUE = '#2365a5'
INK = '#172c40'


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((SNAPSHOT / 'provenance.json').read_text())
    chosen = [s for s in manifest['selected'] if s['system'] == 'baseline']
    tags = [s['tag'] for s in chosen]
    assert len(tags) == 10 and set(tags) == set(ARMS.values())
    sources = {SNAPSHOT / 'raw_metrics.tsv', SNAPSHOT / 'provenance.json'}
    loads, identities = {}, {}
    for p in sorted((EXP / 'artifacts/log_loads').glob('**/validated.json')):
        d = json.loads(p.read_text())
        if (d.get('system') == 'baseline' and d.get('dataset_gib') == 1000
                and d.get('key_bytes') == 24 and d.get('value_bytes') == 1000
                and d.get('final_sst_count') in ARMS):
            tag = ARMS[d['final_sst_count']]
            assert tag not in loads, (tag, p)
            assert d['status'] == 'validated', (tag, d['status'])
            loads[tag] = d
            sources.add(p)
            identities[tag] = dict(validated=str(p),source_identity_file=d['source_identity_file'],
                                   binary_sha256=d['binary_sha256'],db_dir=d['db_dir'])
    assert set(loads) == set(tags)
    rows = list(csv.DictReader((SNAPSHOT / 'raw_metrics.tsv').open(), delimiter='\t'))
    ycsb = {(r['series'],r['workload']):r for r in rows if r['system'] == 'baseline'}
    assert len(ycsb) == 60
    specs = [
        ('SST count', 'files', lambda d:d['final_sst_count'], 0),
        ('$L_3$ files', 'files', lambda d:d['levels']['3']['files'], 0),
        ('$L_4$ files', 'files', lambda d:d['levels']['4']['files'], 0),
        ('$L_5$ files', 'files', lambda d:d['levels']['5']['files'], 0),
        ('Final SST size', 'GiB', lambda d:d['final_sst_bytes']/GIB, 2),
        ('Loading WAF', 'ratio', lambda d:d['waf'], 3),
    ]
    panels, measurements = [], []
    for title,unit,getter,decimals in specs:
        vals = [float(getter(loads[tag])) for tag in tags]
        panels.append(dict(title=title,unit=unit,decimals=decimals,values=vals,group='state'))
    for w in 'ABCDEF':
        vals = [float(ycsb[(s['label'],w)]['throughput_ops_sec'])/1000 for s in chosen]
        panels.append(dict(title='YCSB '+w,unit='K ops/s',decimals=1,values=vals,group='workload'))
    ranges = []
    for p in panels:
        vals = p['values']
        lo,hi,mean = min(vals),max(vals),st.mean(vals)
        p.update(minimum=lo,maximum=hi,mean=mean,width_pct=100*(hi-lo)/mean)
        ranges.append({k:v for k,v in p.items() if k not in ['values','decimals']})
        measurements.extend(dict(metric=p['title'],unit=p['unit'],label=s['label'],tag=s['tag'],value=v)
                            for s,v in zip(chosen,vals))
    assert len(measurements) == 120
    tsv(OUT/'measurements.tsv', measurements)
    tsv(OUT/'ranges.tsv', ranges)

    plt.rcParams.update({'font.family':'DejaVu Sans','font.size':8.5,'pdf.fonttype':42})
    fig = plt.figure(figsize=(7.3,5.0))
    grid = fig.add_gridspec(6,2,left=0.055,right=0.975,bottom=0.11,top=0.82,hspace=1.40,wspace=0.24)
    fig.text(0.055,0.965,'Ten independent baseline loadings',fontsize=13,weight='bold',color=INK)
    fig.legend(handles=[Line2D([],[],linestyle='none',marker='o',color=BLUE,markersize=4,label='One loading'),
                        Line2D([],[],color='#bfcede',linewidth=4,label='Observed min–max'),
                        Line2D([],[],linestyle='none',marker='|',color=INK,markersize=9,label='Mean')],
               loc='upper left',bbox_to_anchor=(0.047,0.945),frameon=False,ncol=3,fontsize=8.5)
    fig.text(0.055,0.858,'(a) Final state and loading work',fontsize=10,weight='bold',color=INK)
    fig.text(0.57,0.858,'(b) Subsequent YCSB throughput',fontsize=10,weight='bold',color=INK)
    audits = []
    for i,p in enumerate(panels):
        col,row = (0,i) if i<6 else (1,i-6)
        ax = fig.add_subplot(grid[row,col])
        lo,hi,mean = p['minimum'],p['maximum'],p['mean']
        span = hi-lo
        pad = span*0.10 if span else max(abs(mean)*0.01,1)
        ax.set_xlim(lo-pad,hi+pad)
        ax.set_ylim(-0.72,0.72)
        ax.hlines(0,lo,hi,color='#bfcede',linewidth=5,zorder=1)
        ax.vlines([lo,hi],-0.16,0.16,color='#9aafc3',linewidth=0.7,zorder=2)
        offsets = [0.0,0.32,-0.32,0.16,-0.16,0.48,-0.48,0.24,-0.24,0.40]
        ax.scatter(p['values'],offsets,s=13,color=BLUE,edgecolor='white',linewidth=0.3,zorder=3)
        ax.plot(mean,0,marker='|',color=INK,markersize=9,markeredgewidth=1.2,zorder=4)
        ax.set_yticks([])
        ax.set_xticks([lo,hi])
        fmt = lambda v: format(v,',.'+str(p['decimals'])+'f')
        ax.set_xticklabels([fmt(lo),fmt(hi)],fontsize=8,color='#43586b')
        ax.tick_params(axis='x',length=0,pad=2)
        for spine in ax.spines.values():
            spine.set_visible(False)
        ax.text(0,1.14,p['title']+' ('+p['unit']+')',transform=ax.transAxes,fontsize=9,color=INK)
        ax.text(1,1.14,'span '+format(p['width_pct'],'.2f')+'%',transform=ax.transAxes,
                ha='right',fontsize=9,color=INK,weight='bold')
        audits.append(dict(metric=p['title'],observations=10,xmin=lo-pad,xmax=hi+pad,
                           endpoints=[lo,hi],axis_scale='linear',independently_zoomed=True))
    fig.text(0.055,0.041,'Actual units; each horizontal axis is independently zoomed. Span = (max − min) / mean.',fontsize=8,color='#43586b')
    fig.text(0.055,0.015,'1,000 GiB input; 24 B keys + 1,000 B values. YCSB: 50 GiB cache, 48 threads, 300 s/workload.',fontsize=8,color='#43586b')
    fig.savefig(OUT/'figure6_intervals.png',dpi=240)
    fig.savefig(OUT/'figure6_intervals.pdf')
    plt.close(fig)

    # Directly compare the observed relative spread; retain actual endpoints
    # beside the shared axis, and retain all observations in the detail view.
    fig = plt.figure(figsize=(7.3,4.5))
    ax = fig.add_axes([0.20,0.19,0.48,0.67])
    positions = list(range(6)) + list(range(7,13))
    for y,p in zip(positions,panels):
        width = p['width_pct']
        ax.hlines(y,0,width,color='#9cb7d2',linewidth=1.8,zorder=2)
        ax.scatter([width],[y],s=24,color=BLUE,zorder=3)
        ax.text(width+0.20,y,format(width,'.2f')+'%',va='center',fontsize=9,color=INK)
        fmt = lambda v: format(v,',.'+str(p['decimals'])+'f')
        value_range = fmt(p['minimum'])+'–'+fmt(p['maximum'])
        if p['unit'] != 'files':
            value_range += ' '+p['unit']
        ax.text(1.06,y,value_range,transform=ax.get_yaxis_transform(),va='center',fontsize=9,color=INK)
    ax.set_yticks(positions)
    ax.set_yticklabels([p['title'] for p in panels],fontsize=9)
    ax.set_ylim(12.7,-0.7)
    ax.set_xlim(0,10)
    ax.set_xticks(range(0,11,2))
    ax.set_xticklabels([str(n)+'%' for n in range(0,11,2)],fontsize=9)
    ax.set_xlabel('Observed range / mean',fontsize=10,labelpad=7)
    ax.grid(axis='x',color='#e2e8ed',linewidth=0.65,zorder=0)
    ax.axhline(6,color='#d1dae3',linewidth=0.7)
    ax.tick_params(axis='y',length=0,pad=8)
    for side in ['top','right','left']:
        ax.spines[side].set_visible(False)
    ax.spines['bottom'].set_color('#9aafc3')
    fig.text(0.045,0.955,'Observed variation across ten baseline loadings',fontsize=12,weight='bold',color=INK)
    fig.text(0.045,0.900,'Metric',fontsize=9,weight='bold',color=INK)
    fig.text(0.711,0.900,'Actual min–max',fontsize=9,weight='bold',color=INK)
    fig.text(0.045,0.045,'Each mark summarizes 10 loads. Range = max − min; percentages use the metric’s own mean.',fontsize=8,color='#43586b')
    fig.text(0.045,0.015,'Observed sample ranges; individual values are retained in the accompanying detail plot.',fontsize=8,color='#43586b')
    fig.savefig(OUT/'figure6_spread.png',dpi=240)
    fig.savefig(OUT/'figure6_spread.pdf')
    plt.close(fig)
    sources.update([Path(__file__).resolve(),Path(__file__).with_name('plot_ch3_band.py').resolve()])
    prov=dict(created_utc=datetime.now(timezone.utc).isoformat(),baseline_sources=identities,
              frozen_ycsb_snapshot=str(SNAPSHOT),selected=chosen,panel_audit=audits,
              observations=len(measurements),definition='Observed min–max; not a confidence interval or acceptance threshold.',
              overview_axis=dict(metric='100 * (max - min) / mean',xmin=0,xmax=10,unit='%',summaries=12),
              source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)},
              reproduce_argv=['python3','experiments/analysis/plot_figure6_intervals.py'])
    (OUT/'provenance.json').write_text(json.dumps(prov,indent=2)+'\n')
    print('Preview:',OUT)
    for p in panels:
        print(p['title'],p['minimum'],p['maximum'],format(p['width_pct'],'.4f')+'%')


if __name__ == '__main__':
    main()
