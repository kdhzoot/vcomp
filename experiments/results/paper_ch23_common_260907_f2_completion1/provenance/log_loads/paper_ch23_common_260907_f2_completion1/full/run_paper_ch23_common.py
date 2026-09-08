#!/usr/bin/env python3
"""Run the approved common-build pilot or overnight campaign, preserving evidence."""
import argparse
import fcntl
import json
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'lib'))
from ch23_common import (BASE, CLEAN, CONFIGS, EXPERIMENTS, F2, GIB, HASHES,
    REPO, SYSTEMS, TIB, WORKSPACE, active_benchmarks, audit_options, bench_stats,
    check_binary, command, db_identity, levels, load_options, measure,
    read_metrics, read_options, require, save_json, sha, stage_db, ticker)


class Campaign:
    def __init__(self, args):
        self.args = args
        self.gib = 1 if args.phase == 'pilot' else 1000
        self.root = EXPERIMENTS / 'artifacts' / 'log_loads' / args.run_id / args.phase
        self.dbroot = Path('/work/vcomp/exp') / args.run_id / args.phase
        self.readroot = EXPERIMENTS / 'artifacts' / 'log_runs' / args.run_id / args.phase
        self.loads, self.reads = {}, {}

    def event(self, kind, message):
        row = dict(utc=time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), kind=kind, message=message)
        print('[{}] {} {}'.format(row['utc'], kind, message), flush=True)
        with (self.root / 'events.jsonl').open('a') as f:
            f.write(json.dumps(row) + '\n')
        save_json(self.root / 'status.json', row)

    def prepare(self):
        require(not active_benchmarks(), 'another benchmark is active')
        for b in (CLEAN, F2):
            check_binary(b)
        require(subprocess.check_output(['git', '-C', str(CLEAN.parent), 'rev-parse', 'HEAD'], text=True).strip()
                == 'f455ab7bd6a8c67f00d48075bb310f131d9fae5f', 'canonical source revision changed')
        require(not subprocess.check_output(['git', '-C', str(CLEAN.parent), 'status', '--porcelain'], text=True),
                'canonical source is dirty')
        if self.args.phase == 'full':
            pilot = Path(self.args.pilot_root)
            require((pilot / 'COMPLETED').is_file(), 'required complete pilot evidence missing')
            manifest = json.loads((pilot / 'manifest.json').read_text())
            require(manifest['hashes'] == HASHES, 'pilot executable mismatch')
            require(manifest['runner_sha256'] == sha(__file__), 'runner changed since pilot')
            require(manifest['library_sha256'] == sha(EXPERIMENTS/'lib'/'ch23_common.py'),
                    'shared library changed since pilot')
            if not self.args.resume:
                require(shutil.disk_usage('/work').free >= 12*TIB, '12-TiB initial campaign budget unavailable')
        if self.args.resume:
            require(self.root.is_dir(), 'resume root missing')
            manifest = json.loads((self.root/'manifest.json').read_text())
            require(manifest['hashes'] == HASHES, 'resume binary mismatch')
            for attr in ('loads', 'reads'):
                path = self.root / (attr+'.json')
                if path.exists():
                    setattr(self, attr, json.loads(path.read_text()))
        else:
            for root in (self.root, self.dbroot, self.readroot):
                require(not root.exists(), 'fresh output already exists: '+str(root))
                root.mkdir(parents=True)
            save_json(self.root/'manifest.json', dict(
                phase=self.args.phase, run_id=self.args.run_id, dataset_gib=self.gib,
                hashes=HASHES, runner_sha256=sha(__file__),
                library_sha256=sha(EXPERIMENTS/'lib'/'ch23_common.py'),
                approved_scope='P,A,B,C; deferred scaling/breakdown excluded',
                pilot_root=self.args.pilot_root, common_options=BASE))
            for p in (Path(__file__), EXPERIMENTS/'lib'/'ch23_common.py'):
                shutil.copy2(str(p), str(self.root/p.name))
            for name, repo in (('clean', CLEAN.parent), ('f2load', REPO)):
                for desc, argv in (('commit',['rev-parse','HEAD']), ('status',['status','--short']), ('diff',['diff','--binary'])):
                    (self.root/(name+'.'+desc)).write_bytes(subprocess.check_output(['git','-C',str(repo)]+argv))
            with (self.root/'environment.txt').open('w') as f:
                for cmd in (['uname','-a'], ['lscpu'], ['free','-b'], ['df','-B1','/work'],
                            ['findmnt','/work'], ['lsblk','-o','NAME,MODEL,SIZE,TYPE,MOUNTPOINT'], ['swapon','--show']):
                    subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, check=True)
                f.write(Path('/proc/mdstat').read_text())
        self.event('READY', self.args.phase)

    def load_case(self, mode, kv=1024):
        case = mode + ('_1kb' if kv == 1024 else '_91b')
        if case in self.loads:
            require((Path(self.loads[case]['db_dir'])/'CURRENT').is_file(), 'validated source DB missing')
            self.event('REUSE_COMPLETED', case)
            return self.loads[case]
        log, db = self.root/case, self.dbroot/case
        binary = F2 if mode == 'f2load' else CLEAN
        source_identity = None
        if mode == 'last_comp':
            prefix = self.loads['flush_only_1kb']
            source_db = Path(prefix['db_dir'])
            source_identity = stage_db(source_db, db)
        else:
            require(not db.exists(), 'DB already exists without validated measurement: '+str(db))
        o = load_options(self.gib, kv, db, log/'phase1', mode)
        phase, text = measure(command(binary, o), log/'phase1', self.event)
        n = o['num']
        benchmark = o['benchmarks'].split(',')[0]
        stats = bench_stats(text, benchmark)
        if mode == 'f2load':
            require('FillVirtual: generating {} keys...'.format(n) in text, 'F2Load input count mismatch')
            require(re.search(r'Phase 2 \(materialization\): .*?, [1-9][0-9]* keys written', text),
                    'F2Load materialization did not complete')
        elif mode == 'last_comp':
            require(stats['operations'] == 1, 'manual compact was not called once')
            count = re.findall(r'^rocksdb.compaction.times.micros .*?COUNT\s*:\s*(\d+)', text, re.M)
            require(count and int(count[-1]) == 1, 'expected one final compaction job')
        else:
            require(stats['operations'] == n and ticker(text, 'rocksdb.number.keys.written') == n,
                    'input operation count mismatch: '+case)
        if mode not in ('flush_only','last_comp'):
            require(re.search(r'waitforcompaction\(.*\): finished with status \(OK\)', text), 'load did not drain')
        audit_options(db, log/'phase1', o)
        phases = [phase]
        flush_bytes = ticker(text, 'rocksdb.flush.write.bytes')
        compaction_bytes = ticker(text, 'rocksdb.compact.write.bytes')
        input_bytes = n*kv
        if mode == 'fillseq_ow':
            ow = dict(o, report_file=str(log/'phase2'/'report.rep'), use_existing_db=True,
                      writes=n//10, benchmarks='overwrite,flush,compact0,waitforcompaction,stats,levelstats')
            phase2, text = measure(command(CLEAN, ow), log/'phase2', self.event)
            require(bench_stats(text,'overwrite')['operations'] == n//10, 'overwrite count mismatch')
            require(ticker(text,'rocksdb.number.keys.written') == n//10, 'overwrite write ticker mismatch')
            require(re.search(r'waitforcompaction\(.*\): finished with status \(OK\)', text), 'overwrite did not drain')
            audit_options(db, log/'phase2', ow)
            phases.append(phase2)
            flush_bytes += ticker(text, 'rocksdb.flush.write.bytes')
            compaction_bytes += ticker(text, 'rocksdb.compact.write.bytes')
            input_bytes += (n//10)*kv
        layout = levels(text)
        if mode == 'flush_only':
            require(compaction_bytes == 0 and ticker(text,'rocksdb.compact.read.bytes') == 0,
                    'unexpected flush-only compaction')
            require(layout[0]['files'] > 0 and sum(layout[i]['files'] for i in range(1,7)) == 0,
                    'flush-only state is not L0-only')
        else:
            pending = re.findall(r'Estimated pending compaction bytes:\s*(\d+)', text)
            require(pending and int(pending[-1]) == 0, 'pending compaction remains')
        if source_identity is not None:
            require(db_identity(source_db) == source_identity, 'Flush-only source changed during Last-comp')
            save_json(log/'source_identity.json', source_identity)
        elapsed = sum(p['elapsed_sec'] for p in phases)
        if mode == 'last_comp':
            elapsed += prefix['elapsed_sec']
            flush_bytes += prefix['flush_sst_write_bytes']
            compaction_bytes += prefix['compaction_sst_write_bytes']
        ident = db_identity(db)
        result = dict(case_id=case, system=mode, dataset_gib=self.gib,
            key_bytes=o['key_size'], value_bytes=o['value_size'], num_keys=n,
            logical_input_bytes=input_bytes, elapsed_sec=elapsed,
            loading_min=elapsed/60, benchmark=stats, phases=phases, levels=layout,
            peak_rss_kb=max(p['peak_rss_kb'] for p in phases),
            flush_sst_write_bytes=flush_bytes, compaction_sst_write_bytes=compaction_bytes,
            total_sst_write_bytes=flush_bytes+compaction_bytes,
            waf=(flush_bytes+compaction_bytes)/input_bytes if mode!='f2load' else None,
            final_sst_bytes=sum(item[1] for item in ident['ssts'].values()),
            final_sst_count=len(ident['ssts']), source_identity_file=str(log/'db_identity.json'),
            binary_sha256=HASHES[str(binary)], db_dir=str(db), log_dir=str(log), repetitions=1)
        if mode == 'last_comp':
            result['shared_prefix_case'] = prefix['case_id']
        save_json(log/'db_identity.json', ident)
        # Full-domain sequential writes guarantee all keys; random loads are
        # checked with the load seed, which selects already-submitted keys.
        verify_reads = 100 if mode == 'flush_only' else 10000
        if self.gib == 1:
            verify_reads = 1000
        if mode == 'f2load':
            verify_reads = 1000
        verify = self.read_case(mode, 'D_pinned_5pct', kv=kv, duration=0, threads=1,
                                reads=verify_reads, seed=12345678,
                                source=result, verification=True)
        if mode != 'f2load':
            require(verify['successful_gets'] == verify_reads, 'known inserted-key reopen check failed: '+case)
        else:
            require(verify['successful_gets'] > 0, 'F2Load source cannot be read by common release')
        result['validation'] = '{}_of_{}_found_after_readonly_reopen'.format(verify['successful_gets'], verify_reads)
        result['status'] = 'validated'
        save_json(log/'validated.json', result)
        self.loads[case] = result
        save_json(self.root/'loads.json', self.loads)
        self.event('VALIDATED_LOAD', '{} {:.3f}s'.format(case, elapsed))
        return result

    def read_case(self, system, config, kv=1024, duration=300, threads=48,
                  reads=10000000000, seed=87654321, source=None, verification=False):
        case = system + ('_1kb' if kv==1024 else '_91b')
        src = source or self.loads[case]
        rid = ('verify_'+case) if verification else config+'/'+system
        if not verification and rid in self.reads:
            self.event('REUSE_COMPLETED_READ', rid)
            return self.reads[rid]
        log = self.readroot/rid
        staged = self.dbroot/'read_staging'/rid
        identity = stage_db(src['db_dir'], staged)
        o = read_options(self.gib, kv, staged, log, config, duration, threads, reads, seed)
        execution, text = measure(command(CLEAN, o), log, self.event)
        metrics = read_metrics(text, o['value_size'])
        if duration:
            require(duration-1 <= metrics['measured_seconds'] <= duration+5,
                    'read duration out of bounds')
        else:
            require(metrics['operations'] == reads, 'verification read count mismatch')
        require(db_identity(src['db_dir']) == identity, 'reader changed source DB')
        save_json(log/'source_identity.json', identity)
        metrics.update(execution)
        metrics.update(system=system, config_id=config, status='ok',
            peak_rss_gib=execution['peak_rss_kb']/1024**2,
            source_db_dir=src['db_dir'], source_loading_case=case,
            source_loading_elapsed_sec=src['elapsed_sec'], result_dir=str(log),
            cache_size_bytes=o['cache_size'], metadata_placement='cache' if o['cache_index_and_filter_blocks'] else 'pinned',
            reader='readrandom', read_seed=seed, duration_sec=duration, threads=threads)
        save_json(log/'validated.json', metrics)
        # Only remove disposable staging created by this call, after success.
        require(staged.is_relative_to(self.dbroot) if hasattr(staged,'is_relative_to')
                else self.dbroot in staged.parents, 'staging cleanup escaped campaign')
        shutil.rmtree(str(staged))
        if not verification:
            self.reads[rid] = metrics
            save_json(self.root/'reads.json', self.reads)
        return metrics

    def run(self):
        self.prepare()
        for mode in SYSTEMS:
            self.load_case(mode)
        if self.args.phase == 'pilot':
            self.load_case('flush_only',91)
            self.load_case('baseline',91)
            for config in CONFIGS:
                self.read_case('baseline',config,duration=30)
            self.read_case('flush_only','A_cache_zero',duration=30)
            self.read_case('flush_only','D_pinned_5pct',duration=30)
            self.read_case('f2load','D_pinned_5pct',duration=30)
        else:
            orders = (SYSTEMS, SYSTEMS[::-1], SYSTEMS[2:]+SYSTEMS[:2], (SYSTEMS[2:]+SYSTEMS[:2])[::-1])
            for config, order in zip(CONFIGS,orders):
                for system in order:
                    self.read_case(system,config)
            self.load_case('flush_only',91)
            self.load_case('baseline',91)
        require(len(self.loads)==8, 'incomplete load matrix')
        require(len(self.reads)==(7 if self.args.phase=='pilot' else 24), 'incomplete read matrix')
        (self.root/'COMPLETED').write_text(time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())+'\n')
        self.event('COMPLETED', str(self.root))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id',required=True)
    parser.add_argument('--phase',choices=('pilot','full'),required=True)
    parser.add_argument('--pilot-root',default='')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    args = parser.parse_args()
    require(re.fullmatch(r'paper_ch23_common_[a-zA-Z0-9_]+',args.run_id), 'invalid campaign run ID')
    c = Campaign(args)
    if args.dry_run:
        for mode in SYSTEMS:
            o = load_options(c.gib,1024,c.dbroot/mode,c.root/mode,mode)
            print(json.dumps(command(F2 if mode=='f2load' else CLEAN,o)))
        for mode in ('baseline','flush_only'):
            print(json.dumps(command(CLEAN,load_options(c.gib,91,c.dbroot/(mode+'_91b'),c.root/mode,mode))))
        for config in CONFIGS:
            print(json.dumps(command(CLEAN,read_options(c.gib,1024,c.dbroot/'STAGED',c.readroot/config,config))))
        return
    signal.signal(signal.SIGTERM, lambda signum, frame: (_ for _ in ()).throw(KeyboardInterrupt()))
    lockpath = EXPERIMENTS/'artifacts'/'paper_ch23_common.lock'
    with lockpath.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:
            c.run()
        except BaseException as exc:
            if c.root.exists():
                (c.root/'FAILED.txt').write_text(traceback.format_exc())
                c.event('FAILED',str(exc))
            raise


if __name__=='__main__':
    main()
