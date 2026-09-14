#!/usr/bin/env python3
"""Rerun chapter 3.1 on retained DBs with uniform request keys only."""
import argparse,csv,fcntl,json,os,shutil,signal,sys,time,traceback
from pathlib import Path
EXP=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(EXP/'scripts/read'),str(EXP/'scripts/paper'),str(EXP/'lib')]
import run_ycsb_alternatives as ycsb
import run_ch3_write_fixed_ops as fixed
from ch23_common import GIB,command,db_identity,levels,require,save_json,sha,stage_db
from parse_ycsb_alternatives import parse_metrics
SYSTEMS=('baseline','fillseq_sparse','flush_only')
ycsb.SYSTEMS=SYSTEMS

class UniformCampaign(ycsb.Campaign):
    def cell_options(self,system,workload,db,log,pilot=False):
        src=self.sources[system+'_1kb']
        if workload=='workloada':
            opts=fixed.options(src,db,log,workload,(48000 if pilot else 210000000)//48,self.cache_size)
            opts['ycsb_requestdistribution']='uniform'
        else:
            opts=ycsb.options(src,db,log,workload,3 if pilot else 300,self.cache_size,'uniform')
        return opts

    def audit_references(self):
        refs={
          'baseline':EXP/'artifacts/log_runs/ycsb_band_run3_260910/full/workloadc/baseline/raw/command.json',
          'flush_only':EXP/'artifacts/log_runs/ch3_ycsb_cache50_260911_run2/full/workloadc/flush_only/raw/command.json',
          'fillseq_sparse':EXP/'artifacts/log_runs/fillseq_sparse_fixed_a_260913_run1/full_extra/fixed_a/fillseq_sparse/raw/command.json'}
        audits=[]
        for system,ref in refs.items():
            require(ref.is_file(),'reference command unavailable '+str(ref))
            wl='workloada' if system=='fillseq_sparse' else 'workloadc'
            opts=self.cell_options(system,wl,Path('/tmp/audit-db'),Path('/tmp/audit-log'))
            expected=dict(x[2:].split('=',1) for x in json.loads(ref.read_text())[1:])
            actual={k:str(v).lower() if isinstance(v,bool) else str(v) for k,v in opts.items()}
            ignored={'db','report_file','ycsb_requestdistribution'}
            require({k:v for k,v in expected.items() if k not in ignored}=={k:v for k,v in actual.items() if k not in ignored},'reference option mismatch '+system)
            audits.append(dict(system=system,reference=str(ref),reference_sha256=sha(ref),changed_option='ycsb_requestdistribution: zipfian -> uniform',ignored_paths=['db','report_file']))
        return audits

    def export(self):
        save_json(self.root/'results.json',self.rows)
        keys=['phase','system','workload','status','operations','engine_keys_read','engine_keys_written','throughput_ops_sec','avg_latency_us','filter_checks_per_lookup','positive_lookup_pct','compaction_write_bytes','compaction_read_bytes','flush_write_bytes','pending_bytes_end','process_elapsed_sec','log_dir','clone_db_dir']
        with (self.root/'summary.tsv').open('w') as f:
            w=csv.DictWriter(f,fieldnames=keys,delimiter='\t',extrasaction='ignore');w.writeheader();w.writerows(self.rows)

    def cell(self,phase,system,workload):
        pilot=phase=='pilot'
        self.current=dict(phase=phase,system=system,workload=workload)
        src=self.sources[system+'_1kb'];db=self.dbroot/phase/workload/system;log=self.root/phase/workload/system
        self.event('STAGING',source=src['db_dir'],destination=str(db))
        before=stage_db(src['db_dir'],db);require(before==self.identities[system],'source changed')
        opts=self.cell_options(system,workload,db,log,pilot)
        timeout=180 if pilot else (3600 if system=='flush_only' and workload=='workloada' else 7200 if workload=='workloada' else 900)
        execution,text=self.measure(command(self.binary,opts),log,timeout)
        require(db_identity(src['db_dir'])==before,'SOURCE DB CHANGED')
        save_json(log/'source_identity.json',before)
        row=dict(phase=phase,system=system,workload=workload,source_db_dir=src['db_dir'],clone_db_dir=str(db),log_dir=str(log),binary_sha256=ycsb.BINARY_HASH,request_distribution='uniform',**execution)
        if execution['timed_out']:
            require(not pilot and system=='flush_only','unexpected timeout')
            row.update(status='timeout',pending_bytes_end=fixed.pending_bytes(text))
            save_json(log/'validated.json',row);self.rows.append(row);self.export()
            self.event('CELL_FINISHED',status=row['status'],log=str(log));return
        require(execution['exit_code']==0,'benchmark failed')
        self.audit_options(db,log,opts)
        metrics=parse_metrics(text,workload)
        require(not metrics['missing_tickers'] and metrics['operation_histograms'],'missing statistics')
        if workload=='workloada':
            require(metrics['operations']==(48000 if pilot else 210000000),'fixed operations mismatch')
            require(fixed.pending_bytes(text)==0,'pending compaction incomplete')
            require('waitforcompaction' in text and 'status (OK)' in text,'missing successful drain')
        else:
            require(abs(metrics['measured_seconds']-(3 if pilot else 300))<=5,'duration mismatch')
        row.update(metrics,status='ok',pending_bytes_end=fixed.pending_bytes(text),final_levels=levels(text))
        ticker=metrics['tickers']
        require(ticker['rocksdb.bloom.filter.prefix.checked']==0,'unexpected prefix filter checks')
        row['filter_checks_per_lookup']=(ticker['rocksdb.bloom.filter.useful']+ticker['rocksdb.bloom.filter.full.positive'])/metrics['engine_keys_read'] if metrics['engine_keys_read'] else None
        row['positive_lookup_pct']=100*metrics['get_found_fraction'] if metrics['get_found_fraction'] is not None else None
        save_json(log/'validated.json',row);self.rows.append(row);self.export()
        self.event('CELL_FINISHED',status='ok',log=str(log),clones_retained=True)

    def run(self):
        lock=open('/work/vcomp/exp/pebble_current_campaign.lock','a')
        queue=EXP/'artifacts/queues'/self.args.run_id
        queue.mkdir(parents=True,exist_ok=True)
        while True:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                if not ycsb.active_benchmarks():break
                fcntl.flock(lock,fcntl.LOCK_UN)
            except BlockingIOError:pass
            save_json(queue/'STATUS.json',dict(status='waiting',reason='another benchmark or campaign owns server',supervisor_pid=os.getpid(),updated_utc=ycsb.utc()))
            time.sleep(15)
        save_json(queue/'STATUS.json',dict(status='started',supervisor_pid=os.getpid(),run_root=str(self.root),updated_utc=ycsb.utc()))
        try:
            audit=self.audit_references()
            self.prepare()
            self.cells=[(s,'workloadc') for s in SYSTEMS]+[(s,'workloada') for s in SYSTEMS]
            m=json.loads((self.root/'manifest.json').read_text())
            m.update(protocol='chapter31_uniform_C_300s_A_210M_pending_compaction_drain',full_order=self.cells,
                     request_distribution='uniform',C_seconds=300,A_operations=210000000,
                     A_tail='stats,waitforcompaction,stats,levelstats (no extra memtable flush)',
                     timeout_seconds=dict(C=900,A=7200,flush_only_A=3600),retain_all_databases=True,
                     input_keyspace=1048576000,source_bundle_rows=self.sources)
            save_json(self.root/'manifest.json',m);save_json(self.root/'reference_option_audit.json',audit)
            for p in [Path(__file__),Path(fixed.__file__),EXP/'docs/PAPER_CH31_UNIFORM_RERUN.md']:
                shutil.copy2(p,self.root/'provenance'/p.name)
            for s in SYSTEMS:self.cell('pilot',s,'workloadc')
            for s in ('baseline','fillseq_sparse'):self.cell('pilot',s,'workloada')
            self.verify_sources()
            save_json(self.root/'PILOT_COMPLETED.json',dict(status='ok',cells=5,utc=ycsb.utc()))
            self.event('FULL_START')
            for s,w in self.cells:self.cell('full',s,w)
            full=[r for r in self.rows if r['phase']=='full']
            a=[r for r in full if r['workload']=='workloada' and r['status']=='ok']
            require(len({(r['engine_keys_read'],r['engine_keys_written']) for r in a})==1,'uniform A read/write counts differ between completed systems')
            self.verify_sources()
            dst=EXP/'results'/self.args.run_id;dst.mkdir(exist_ok=False)
            for name in ['manifest.json','reference_option_audit.json','results.json','summary.tsv','PILOT_COMPLETED.json']:
                shutil.copy2(self.root/name,dst/name)
            shutil.copytree(self.root/'provenance',dst/'provenance')
            for row in full:
                src=Path(row['log_dir']);d=dst/'evidence'/row['workload']/row['system'];d.mkdir(parents=True)
                for name in ['validated.json','bench.out']:
                    shutil.copy2(src/name,d/name)
                for name in ['command.json','cache_reset.log','execution.json']:
                    shutil.copy2(src/'raw'/name,d/name)
            completion=dict(status='complete_with_timeout' if any(r['status']!='ok' for r in full) else 'complete',full_cells=len(full),valid_cells=sum(r['status']=='ok' for r in full),sources_preserved=True,finished_utc=ycsb.utc())
            save_json(dst/'COMPLETED.json',completion);save_json(self.root/'COMPLETED.json',completion)
            self.current=None;self.event('COMPLETED',**completion,result_dir=str(dst))
        except BaseException as e:
            self.terminate()
            if self.root.exists():
                (self.root/'failure.txt').write_text(traceback.format_exc());self.event('FAILED',error=str(e))
            raise
        finally:
            if self.identities:self.verify_sources()
            for fd in self.locks:os.close(fd)
            lock.close()

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-id',required=True);p.add_argument('--source-bundle',required=True);p.add_argument('--dry-run',action='store_true')
    p.set_defaults(systems=','.join(SYSTEMS),workloads='ac',duration=300,cache_size=50*GIB,request_distribution='uniform',exclude_flush_only=False,reuse_run=None)
    args=p.parse_args();c=UniformCampaign(args)
    if args.dry_run:print(json.dumps(c.audit_references(),indent=2));return
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(KeyboardInterrupt()))
    c.run()
if __name__=='__main__':main()
