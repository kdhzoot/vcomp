#!/usr/bin/env python3
"""Finish F2Load under the common zero-pending endpoint, then measure its reads."""
import argparse
import fcntl
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
import traceback

from run_paper_ch23_common import Campaign
from ch23_common import (BASE, CLEAN, CONFIGS, EXPERIMENTS, F2, GIB, HASHES,
    REPO, TIB, active_benchmarks, audit_options, bench_stats, check_binary,
    command, db_identity, levels, load_options, measure, require, save_json,
    sha, stage_db, ticker)

PREVIOUS = EXPERIMENTS / 'artifacts/log_loads/paper_ch23_common_260905_approved_run3/full'
LIBRARY = EXPERIMENTS / 'lib/ch23_common.py'


def pending(text):
    values = re.findall(r'Estimated pending compaction bytes:\s*(\d+)', text)
    require(values, 'missing pending-compaction property')
    return int(values[-1])


class F2Completion(Campaign):
    def prepare(self):
        require(not active_benchmarks(), 'another storage benchmark is active')
        for binary in (CLEAN, F2):
            check_binary(binary)
        require(not subprocess.check_output(['git','-C',str(CLEAN.parent),'status','--porcelain'],text=True),
                'canonical source changed')
        require(subprocess.check_output(['git','-C',str(CLEAN.parent),'rev-parse','HEAD'],text=True).strip()
                == 'f455ab7bd6a8c67f00d48075bb310f131d9fae5f', 'canonical revision changed')
        pilot = self.root.parent / 'pilot'
        if self.args.phase == 'full':
            require((pilot/'COMPLETED').is_file(), 'F2 completion pilot missing')
            qualification = json.loads((pilot/'manifest.json').read_text())
            require(qualification['runner_sha256'] == sha(__file__), 'runner changed after pilot')
            require(qualification['library_sha256'] == sha(LIBRARY), 'library changed after pilot')
            require(qualification['hashes'] == HASHES, 'pilot binary mismatch')
            require(shutil.disk_usage('/work').free >= 4*TIB, 'insufficient full-run free space')
            self.loads = json.loads((PREVIOUS/'loads.json').read_text())
            self.reads = json.loads((PREVIOUS/'reads.json').read_text())
            require(len(self.loads)==7 and len(self.reads)==20, 'previous matrix incomplete')
            for case, row in self.loads.items():
                require(row['status']=='validated', 'unvalidated prior load: '+case)
                require(db_identity(row['db_dir']) == json.loads(Path(row['source_identity_file']).read_text()),
                        'prior source DB changed: '+case)
        for directory in (self.root, self.dbroot, self.readroot):
            require(not directory.exists(), 'fresh output already exists: '+str(directory))
            directory.mkdir(parents=True)
        save_json(self.root/'manifest.json', dict(
            phase=self.args.phase, run_id=self.args.run_id, dataset_gib=self.gib,
            hashes=HASHES, runner_sha256=sha(__file__), library_sha256=sha(LIBRARY),
            parent_runner_sha256=sha(Path(__file__).with_name('run_paper_ch23_common.py')),
            common_options=BASE, reused_campaign=str(PREVIOUS) if self.args.phase=='full' else None,
            reused_loads_sha256=sha(PREVIOUS/'loads.json'), reused_reads_sha256=sha(PREVIOUS/'reads.json'),
            approved_scope='Fresh F2Load + timed clean settling + four reads; existing 7 loads/20 reads reused',
            load_protocol='frozen F2Load then frozen clean automatic settling; both process wall times counted',
            pilot_root=str(pilot), read_order=list(CONFIGS),
            timing_limit='F2Load measured after the earlier control campaign, not interleaved'))
        for path in (Path(__file__), LIBRARY, Path(__file__).with_name('run_paper_ch23_common.py')):
            shutil.copy2(path, self.root/path.name)
        for tag, repo in [('clean',CLEAN.parent),('f2load',REPO)]:
            for suffix, argv in [('commit',['rev-parse','HEAD']),('status',['status','--short']),('diff',['diff','--binary'])]:
                (self.root/(tag+'.'+suffix)).write_bytes(subprocess.check_output(['git','-C',str(repo)]+argv))
        with (self.root/'environment.txt').open('w') as f:
            for argv in (['uname','-a'],['lscpu'],['free','-b'],['df','-B1','/work'],['findmnt','/work'],['swapon','--show']):
                subprocess.run(argv,stdout=f,stderr=subprocess.STDOUT,check=True)
        self.event('READY', self.args.phase+'; frozen F2Load and clean completion binaries')

    def load_f2(self):
        log = self.root/'f2load_1kb'
        initial = self.dbroot/'f2load_1kb_materialized'
        final = self.dbroot/'f2load_1kb'
        options = load_options(self.gib,1024,initial,log/'phase1','f2load')
        phase1, first = measure(command(F2,options),log/'phase1',self.event)
        n = options['num']
        require('FillVirtual: generating {} keys...'.format(n) in first, 'input count mismatch')
        require(re.search(r'Phase 2 \(materialization\): .*?, [1-9][0-9]* keys written',first),
                'materialization did not complete')
        require(re.search(r'L0 visible window final: .*pending=0 visible=0',first), 'virtual L0 not drained')
        require(re.search(r'waitforcompaction\(.*\): finished with status \(OK\)',first), 'initial wait failed')
        audit_options(initial,log/'phase1',options)
        before_pending = pending(first)
        prep_start = time.time()
        initial_identity = stage_db(initial,final)
        save_json(log/'pre_settle_identity.json', initial_identity)
        save_json(log/'checkpoint.json',dict(elapsed_sec=time.time()-prep_start,
            source=str(initial),destination=str(final),included_in_loading_time=False))
        settle = load_options(self.gib,1024,final,log/'phase2','baseline')
        settle.update(format_version=6,use_existing_db=True,
                      benchmarks='waitforcompaction,stats,levelstats')
        phase2, second = measure(command(CLEAN,settle),log/'phase2',self.event)
        require(re.search(r'waitforcompaction\(.*\): finished with status \(OK\)',second), 'clean settle wait failed')
        require(pending(second)==0, 'clean settling still leaves pending compaction')
        require(ticker(second,'rocksdb.number.keys.written')==0, 'settle unexpectedly inserted keys')
        require(db_identity(initial)==initial_identity, 'pre-settle source changed')
        audit_options(final,log/'phase2',settle)
        identity = db_identity(final)
        save_json(log/'db_identity.json',identity)
        elapsed = phase1['elapsed_sec']+phase2['elapsed_sec']
        flush = sum(ticker(t,'rocksdb.flush.write.bytes') for t in (first,second))
        comp = sum(ticker(t,'rocksdb.compact.write.bytes') for t in (first,second))
        result = dict(case_id='f2load_1kb',system='f2load',dataset_gib=self.gib,
            key_bytes=24,value_bytes=1000,num_keys=n,logical_input_bytes=n*1024,
            elapsed_sec=elapsed,loading_min=elapsed/60,benchmark=bench_stats(first,'fillvirtual'),
            phases=[phase1,phase2],levels=levels(second),
            peak_rss_kb=max(phase1['peak_rss_kb'],phase2['peak_rss_kb']),
            flush_sst_write_bytes=flush,compaction_sst_write_bytes=comp,
            total_sst_write_bytes=flush+comp,waf=None,
            sst_waf_note='F2 materialization bypasses flush/compaction tickers; use separately recorded device writes.',
            final_sst_bytes=sum(v[1] for v in identity['ssts'].values()),final_sst_count=len(identity['ssts']),
            source_identity_file=str(log/'db_identity.json'),binary_sha256=HASHES[str(F2)],
            completion_binary_sha256=HASHES[str(CLEAN)],db_dir=str(final),log_dir=str(log),repetitions=1,
            load_protocol='materialize_then_clean_automatic_settle',
            materialized_process_sec=phase1['elapsed_sec'],settle_process_sec=phase2['elapsed_sec'],
            pending_before_settle=before_pending,pending_after_settle=pending(second),
            settle_compaction_read_bytes=ticker(second,'rocksdb.compact.read.bytes'),
            settle_compaction_write_bytes=ticker(second,'rocksdb.compact.write.bytes'),
            pre_settle_db_dir=str(initial))
        check=self.read_case('f2load','D_pinned_5pct',duration=0,threads=1,
                            reads=1000,seed=12345678,source=result,verification=True)
        require(check['successful_gets']>0,'clean reader found no F2Load values')
        result.update(status='validated',validation='{}_of_1000_found_after_readonly_reopen;zero_pending_after_timed_settle'.format(check['successful_gets']))
        save_json(log/'validated.json',result)
        self.loads['f2load_1kb']=result
        save_json(self.root/'loads.json',self.loads)
        self.event('VALIDATED_LOAD','F2Load {:.3f}s + clean settle {:.3f}s = {:.3f}s; pending {} -> 0'.format(
            phase1['elapsed_sec'],phase2['elapsed_sec'],elapsed,before_pending))

    def run(self):
        self.prepare()
        self.load_f2()
        for config in CONFIGS:
            self.read_case('f2load',config,duration=30 if self.args.phase=='pilot' else 300)
        require(len(self.loads)==(1 if self.args.phase=='pilot' else 8),'load matrix incomplete')
        require(len(self.reads)==(4 if self.args.phase=='pilot' else 24),'read matrix incomplete')
        (self.root/'COMPLETED').write_text(time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())+'\n')
        self.event('COMPLETED','F2Load '+self.args.phase+' validated')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--phase',choices=['pilot','full','all'],required=True)
    args=parser.parse_args()
    require(re.fullmatch(r'paper_ch23_common_[a-zA-Z0-9_]+',args.run_id),'invalid run ID')
    phases=('pilot','full') if args.phase=='all' else (args.phase,)
    signal.signal(signal.SIGTERM,lambda signum,frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    with (EXPERIMENTS/'artifacts/paper_ch23_common.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        for phase in phases:
            args.phase=phase
            campaign=F2Completion(args)
            try:
                campaign.run()
            except BaseException as error:
                if campaign.root.exists():
                    (campaign.root/'FAILED.txt').write_text(traceback.format_exc())
                    campaign.event('FAILED',str(error))
                raise


if __name__=='__main__':
    main()
