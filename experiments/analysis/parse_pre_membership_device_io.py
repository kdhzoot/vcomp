"""Reconstruct original 10+10 A-F device metrics from retained raw logs.

Read-only analysis. The first iostat report is a since-boot average.
Zero md0 timing/weighted-I/O counters are retained as raw evidence, not latency.
"""
import argparse
import json,re
from pathlib import Path
from datetime import datetime,timezone
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--results-root', type=Path, default=Path(__file__).resolve().parents[1] / 'results')
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
ROOT=args.results_root
BASE=['b01','b02','b03','n01','n02','n03','n04','r01','r02','run3']
TAGS=[('baseline',BASE,'ycsb_band_'),('f2load',[f'f{i:02}' for i in range(1,11)],'ycsb_f2band_')]
def disk(path):
 return {x[2]:list(map(int,x[3:])) for line in path.read_text().splitlines() if len(x:=line.split())>=14}
def cpu(path):
 return list(map(int,path.read_text().splitlines()[0].split()[1:9]))
def avg(xs): return sum(xs)/len(xs) if xs else None
def percent(xs,p):
 xs=sorted(xs); return xs[round((len(xs)-1)*p)] if xs else None
def frames(path):
 out=[]; frame=None; columns=[]
 for line in path.read_text().splitlines():
  words=line.split()
  if words and words[0]=='Device':
   if frame is not None: out.append(frame)
   columns=words[1:];frame={}
  elif frame is not None and len(words)==len(columns)+1:
   try: frame[words[0]]=dict(zip(columns,map(float,words[1:])))
   except ValueError: pass
 if frame is not None: out.append(frame)
 return out[1:] # iostat -dx 5 starts with since-boot average
rows=[]
for system,tags,prefix in TAGS:
 for tag in tags:
  paths=list(ROOT.glob(prefix+tag+'_*/results.json'));assert len(paths)==1
  for result in json.loads(paths[0].read_text()):
   if result['phase']!='full' or result['system']!=system:continue
   raw=Path(result['log_dir'])/'raw';a,b=disk(raw/'diskstats.start'),disk(raw/'diskstats.end')
   execution=json.loads((raw/'execution.json').read_text());elapsed=execution['process_elapsed_sec']
   ds={d:[y-x for x,y in zip(a[d],b[d])] for d in a if d in b and (d=='md0' or re.fullmatch(r'nvme\d+n1',d))}
   members=[d for d in ds if d!='nvme0n1' and d.startswith('nvme')]
   def metrics(delta):
    reads=delta[0];writes=delta[4]
    return dict(read_ios=reads,read_iops=reads/elapsed,read_MiB_s=delta[2]*512/2**20/elapsed,read_await_ms=delta[3]/reads if reads else None,write_iops=writes/elapsed,write_MiB_s=delta[6]*512/2**20/elapsed,write_await_ms=delta[7]/writes if writes else None,util_pct=delta[9]/elapsed/10,aqu_sz=delta[10]/elapsed/1000,read_ms_delta=delta[3],weighted_io_ms_delta=delta[10],busy_ms_delta=delta[9])
   devices={d:metrics(ds[d]) for d in ds};summed=[sum(ds[d][i] for d in members) for i in range(11)]
   summary=metrics(summed);summary['util_pct']=avg([devices[d]['util_pct'] for d in members]);summary['member_count']=len(members);summary['member_max_read_await_ms']=max(devices[d]['read_await_ms'] or 0 for d in members);summary['member_await_p50_ms']=percent([devices[d]['read_await_ms'] or 0 for d in members],.5)
   ca,cb=cpu(raw/'stat.start'),cpu(raw/'stat.end');cd=[y-x for x,y in zip(ca,cb)];total=sum(cd)
   cpu_rows=sum(line.startswith('cpu') for line in (raw/'stat.start').read_text().splitlines())-1
   cp=dict(zip(['user_pct','nice_pct','system_pct','idle_pct','iowait_pct','irq_pct','softirq_pct','steal_pct'],[v/total*100 for v in cd]))
   ff=frames(raw/'iostat.log');samples=[]
   for i,frame in enumerate(ff):
    ms=[frame[d] for d in members if d in frame];nr=sum(x['r/s'] for x in ms)
    samples.append(dict(interval_start_sec=i*5,read_iops=nr,read_MiB_s=sum(x['rkB/s'] for x in ms)/1024,read_await_ms=sum(x['r/s']*x['r_await'] for x in ms)/nr if nr else None,mean_member_util_pct=avg([x['%util'] for x in ms]),max_member_util_pct=max(x['%util'] for x in ms),sum_aqu_sz=sum(x['aqu-sz'] for x in ms),write_MiB_s=sum(x['wkB/s'] for x in ms)/1024,md0=frame.get('md0')))
   windows=[]
   for start in range(0,len(samples),6):
    block=samples[start:start+6];reads=sum(x['read_iops'] for x in block)
    windows.append(dict(start_sec=start*5,end_sec=(start+len(block))*5,read_iops=avg([x['read_iops'] for x in block]),read_MiB_s=avg([x['read_MiB_s'] for x in block]),weighted_read_await_ms=sum((x['read_await_ms'] or 0)*x['read_iops'] for x in block)/reads if reads else None,mean_member_util_pct=avg([x['mean_member_util_pct'] for x in block]),sum_aqu_sz=avg([x['sum_aqu_sz'] for x in block]),write_MiB_s=avg([x['write_MiB_s'] for x in block])))
   maxrun=run=0
   for s in samples:
    run=run+1 if s['mean_member_util_pct']>=99 else 0;maxrun=max(maxrun,run)
   times=(raw/'time.out').read_text();processcpu={}
   for label,key in [('User time (seconds)','user_seconds'),('System time (seconds)','system_seconds')]:
    m=re.search(re.escape(label)+r':\s*(\S+)',times)
    if m:processcpu[key]=float(m[1])
   rows.append(dict(system=system,tag=tag,workload=result['workload'][-1].upper(),throughput_ops_s=result['throughput_ops_sec'],process_elapsed_sec=elapsed,measured_sec=result['measured_seconds'],start_utc=datetime.fromtimestamp(execution['started_epoch'],timezone.utc).isoformat(),end_utc=datetime.fromtimestamp(execution['ended_epoch'],timezone.utc).isoformat(),raw=str(raw),source_result=str(paths[0]),cpu=cp,cpu_snapshot_logical_cpu_rows=cpu_rows,cpu_stat_time_delta_seconds=total/100,process_cpu=processcpu,md0=devices['md0'],nvme_members=summary,per_device=devices,iostat_sample_count=len(samples),iostat_30s_windows=windows,iostat_5s_samples=samples,mean_member_util_ge99_intervals=sum(s['mean_member_util_pct']>=99 for s in samples),longest_mean_member_util_ge99_sec=maxrun*5))
notes=['Cohort exactly initial baseline 10 and f01-f10, full A-F cells, 120 total.','Raw diskstats deltas span full benchmark process including open/drain, divided by execution.json process_elapsed_sec; benchmark measured_seconds recorded separately.','NVMe member aggregate excludes OS nvme0n1; current sysfs md0 RAID0 lists nvme1n1..nvme31n1; historical iostat has the same 31 member names.','Read-weighted await = sum(read_ms deltas) / sum(read IO deltas). No unweighted averaging of device r_await.','iostat -dx 5 omits CPU; first since-boot block excluded. Samples are approximate 5-second intervals from monitor launch, grouped into 30-second windows without filtering. iostat await precision only 0.01 ms; full-process await uses raw diskstats instead.','CPU percentages use aggregate /proc/stat deltas, first eight counters. Snapshots contain 48 logical-CPU rows while iostat header reports 96 CPUs; use recorded aggregate denominator, not an assumed96-core conversion. No temporal CPU samples exist in these logs.','md0 read/write/busy/weighted-time counters remain0. All NVMe weighted-io deltas are0 in C/D/E, and0 or only4-12ms total in A/F despite hundreds of seconds of I/O; queue-depth counters are unavailable/unreliable in this kernel setup. Zero aqu-sz or md0 await/util is not evidence of absent waits.','%util is time with IO in flight; near100% on multi-queue NVMe is not proof of exhausted IOPS/BW or saturation.']
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(dict(method=notes,rows=rows),indent=2))
print(json.dumps({'rows': len(rows), 'output': str(args.output)}))
