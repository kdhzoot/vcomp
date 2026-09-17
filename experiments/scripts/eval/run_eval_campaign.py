#!/usr/bin/env python3
"""Sequential single-point loader for the F2Load evaluation plan.

Every db_bench option that shapes the LSM-Tree is pinned here, not inherited
from RocksDB defaults, so points loaded days apart stay comparable. The option
set is the one that produced fidelity_100gib_20260908_unique_run5.
"""
import argparse, datetime, hashlib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ARTIFACTS = REPO / 'experiments/artifacts'
GIB = 1024 ** 3

# LSM 형태를 결정하는 옵션은 전부 고정한다 (README: Canonical experiment configuration)
COMMON = dict(statistics=1, stats_interval_seconds=60, stats_per_interval=1,
              threads=1, batch_size=1, seed=12345678,
              memtablerep='vector', max_write_buffer_number=16,
              min_write_buffer_number_to_merge=1,
              allow_concurrent_memtable_write=True,
              max_background_jobs=48, subcompactions=1,
              compaction_style=0, num_levels=7,
              level_compaction_dynamic_level_bytes=False,
              level0_file_num_compaction_trigger=4,
              level0_slowdown_writes_trigger=20,
              level0_stop_writes_trigger=36,
              soft_pending_compaction_bytes_limit=64 * 1024**3,
              hard_pending_compaction_bytes_limit=128 * 1024**3,
              disable_wal=True, compression_type='none',
              use_direct_reads=True,
              use_direct_io_for_flush_and_compaction=True,
              format_version=7, bloom_bits=10, enable_index_compression=False,
              keys_per_prefix=0)

# 변인. 값은 기준 point이고, 각 실험이 자기 축만 덮어쓴다.
REFERENCE = dict(key_size=24, value_size=1000,
                 max_bytes_for_level_base=256 * 1024**2,
                 max_bytes_for_level_multiplier=10,
                 write_buffer_size=64 * 1024**2,
                 target_file_size_base=64 * 1024**2,
                 compaction_pri=3)

F2 = dict(use_virtual_compaction=True, plr_error_bound=8,
          # exact membership은 write path의 key stream을 그대로 재생해야 해서
          # Phase 1 keygen이 단일 스레드가 된다(1 TB에서 7.3 s -> 18.5 s). 끄면
          # batch마다 독립 RNG를 써서 keygen이 shard 수만큼 병렬로 돈다.
          vcomp_exact_membership=False,
          # backpressure(기본 on)는 별도 플래그를 넘기지 않는다 — 바이너리 기본값.
          vcomp_register_batch_max=256, vcomp_visible_l0_batch_mb=0,
          vcomp_phase1_shards=8, vcomp_materialize_workers=48,
          vcomp_log_apply_timing=False, vcomp_sort_detail_timing=False)
BENCH = 'flush,compact0,waitforcompaction,stats,levelstats'
EXTRA_ENV = {}          # --env KEY=VAL 로 덮어쓴다 (예: VCOMP_KMV_SAMPLES=256)
RUNTIME_ENV = dict(VCOMP_BG_COMMIT_BATCH_MAX='16',
                   VCOMP_BG_COMMIT_DELAY_US='100')
LIVE_FRACTION = 0.70   # DB bytes / logical input, with headroom for the guard

# point_id, mode, target_gib, key, value, seed, overrides
QUEUE = [
    ('E2-8T', 'f2load',   8000, 24, 1000, None, {}),
    ('E2-8T', 'baseline', 8000, 24, 1000, None, {}),
    ('P0-r1', 'baseline', 1000, 24, 1000, None, {}),
    ('P0-r2', 'baseline', 1000, 24, 1000, None, {}),
    ('P0-r3', 'baseline', 1000, 24, 1000, None, {}),
    ('P0-r1', 'f2load',   1000, 24, 1000, None, {}),
    ('P0-r2', 'f2load',   1000, 24, 1000, None, {}),
    ('P0-r3', 'f2load',   1000, 24, 1000, None, {}),
]


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(8 * 1024**2), b''):
            h.update(chunk)
    return h.hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    tmp.replace(path)


class Campaign:
    def __init__(self, run_id, dbroot, execute):
        self.root = ARTIFACTS / run_id
        self.dbroot = Path(dbroot)
        self.execute = execute
        self.bin = self.root / 'bin'
        self.events = self.root / 'events.jsonl'
        self.done = []

    def event(self, kind, detail):
        line = json.dumps(dict(time=now(), event=kind, detail=detail))
        self.root.mkdir(parents=True, exist_ok=True)
        with self.events.open('a') as f:
            f.write(line + '\n')
        print(f'[{now()}] {kind}: {detail}', flush=True)

    def prepare(self):
        if not self.execute:
            return
        self.bin.mkdir(parents=True, exist_ok=True)
        target = self.bin / 'f2_db_bench'
        if not target.exists():
            shutil.copy2(REPO / 'db_bench', target)
        prov = dict(created=now(), repo=str(REPO), db_root=str(self.dbroot),
                    binary_sha256={'f2_db_bench': sha(target)},
                    source_sha256={p: sha(REPO / p) for p in
                                   ('tools/db_bench_tool.cc',
                                    'experiments/scripts/eval/run_eval_campaign.py')},
                    git_revision=subprocess.check_output(
                        ['git', '-C', str(REPO), 'rev-parse', 'HEAD'], text=True).strip(),
                    common_options=COMMON, reference_variables=REFERENCE,
                    f2_options=F2, queue=[
                        dict(point=p, mode=m, target_gib=g, key_size=k, value_size=v,
                             seed=s, overrides=o) for p, m, g, k, v, s, o in QUEUE])
        save(self.root / 'manifest.json', prov)
        (self.root / 'git_status.txt').write_bytes(
            subprocess.check_output(['git', '-C', str(REPO), 'status', '--short']))
        (self.root / 'git_diff.patch').write_bytes(
            subprocess.check_output(['git', '-C', str(REPO), 'diff', '--binary']))

    def options(self, mode, gib, seed, overrides):
        opts = dict(COMMON, **REFERENCE)
        opts.update(overrides)
        if seed is not None:
            opts['seed'] = seed
        kv = opts['key_size'] + opts['value_size']
        opts.setdefault('num', gib * GIB // kv)
        trace = 'load_trace_file' in opts
        if mode == 'f2load':
            opts.update(F2, benchmarks='fillvirtual,' + BENCH)
            # 큐의 overrides가 F2 기본값(예: vcomp_exact_membership)을 덮어쓸 수 있게 한다
            opts.update({k: v for k, v in overrides.items() if k in F2})
            # F2Load의 flush 배치는 memtable 크기를 따라간다 (E5에서 함께 변한다)
            opts['memtable_flush_size'] = opts['write_buffer_size'] // 1024**2
        else:
            # 트레이스가 있으면 baseload(트레이스 재생), 없으면 fillrandom
            opts['benchmarks'] = ('baseload,' if trace else 'fillrandom,') + BENCH
        return opts

    def run_point(self, point, mode, gib, key, value, seed, overrides):
        label = f'{point}:{mode}'
        log = self.root / f'{point}_{mode}'
        db = self.dbroot / f'{point}_{mode}'
        opts = self.options(mode, gib, seed, dict(overrides,
                            key_size=key, value_size=value))
        opts['db'] = str(db)
        argv = [str(self.bin / 'f2_db_bench')] + [
            f'--{k}={str(v).lower() if isinstance(v, bool) else v}' for k, v in opts.items()]
        need = int(gib * GIB * LIVE_FRACTION * 1.3)
        free = shutil.disk_usage(self.dbroot if self.dbroot.exists() else '/work').free
        if not self.execute:
            print(f'  {label:18} num={opts["num"]:,} db={db} need~{need/1e12:.1f}TB '
                  f'free={free/1e12:.1f}TB')
            return True
        if free < need:
            self.event('SKIPPED_NO_SPACE', f'{label} need={need} free={free}')
            return 'skipped'
        if db.exists():
            self.event('SKIPPED_EXISTS', f'{label} {db}')
            return 'skipped'
        log.mkdir(parents=True, exist_ok=True)
        db.parent.mkdir(parents=True, exist_ok=True)
        save(log / 'argv.json', argv)
        env = {k: v for k, v in os.environ.items() if not k.startswith('VCOMP_')}
        env.update(RUNTIME_ENV)
        env.update(EXTRA_ENV)
        self.event('LOAD_STARTED', label)
        start = time.time()
        with (log / 'stdout_stderr.log').open('wb') as out:
            rc = subprocess.call(argv, stdout=out, stderr=subprocess.STDOUT, env=env)
        elapsed = time.time() - start
        text = (log / 'stdout_stderr.log').read_text(errors='replace')
        bench = opts['benchmarks'].split(',')[0]
        finished = bool(re.search(r'^' + bench + r'\s*:', text, re.MULTILINE))
        size = subprocess.run(['du', '-sb', str(db)], capture_output=True, text=True)
        db_bytes = int(size.stdout.split()[0]) if size.returncode == 0 else None
        record = dict(point=point, mode=mode, target_gib=gib, key_size=key,
                      value_size=value, seed=opts['seed'], overrides=overrides,
                      num_records=opts['num'], exit_code=rc, elapsed_sec=elapsed,
                      benchmark_finished=finished, db_bytes=db_bytes,
                      db_dir=str(db), log_dir=str(log), finished_at=now())
        save(log / 'result.json', record)
        self.done.append(record)
        save(self.root / 'results.json', self.done)
        self.event('LOAD_COMPLETED' if rc == 0 and finished else 'LOAD_FAILED',
                   f'{label} exit={rc} finished={finished} elapsed={elapsed/3600:.2f}h')
        return rc == 0 and finished

    def status(self, phase):
        save(self.root / 'STATUS.json',
             dict(schema='eval_campaign_status_v1', phase=phase, heartbeat=now(),
                  completed=len(self.done), planned=len(QUEUE), pid=os.getpid(),
                  results=[dict(point=r['point'], mode=r['mode'],
                                elapsed_h=round(r['elapsed_sec'] / 3600, 3),
                                ok=r['exit_code'] == 0 and r['benchmark_finished'])
                           for r in self.done]))

    def run(self):
        self.prepare()
        self.status('running')
        for point, mode, gib, key, value, seed, overrides in QUEUE:
            ok = self.run_point(point, mode, gib, key, value, seed, overrides)
            if self.execute:
                self.status('running')
                if ok is not True and ok != 'skipped':
                    self.status('failed')
                    self.event('CAMPAIGN_STOPPED', f'{point}:{mode}')
                    return 1
        if self.execute:
            self.status('completed')
            self.event('CAMPAIGN_COMPLETED', f'{len(self.done)} loads')
        return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id', required=True)
    p.add_argument('--db-root', default=None)
    p.add_argument('--execute', action='store_true')
    p.add_argument('--env', action='append', default=[],
                   help='로딩 프로세스 환경변수 KEY=VAL (반복 가능)')
    p.add_argument('--queue-file', help='JSON list of [point, mode, gib, key, value, seed, overrides]')
    a = p.parse_args()
    dbroot = Path(a.db_root or f'/work/vcomp/exp/{a.run_id}')
    if str(dbroot) == '/' or not str(dbroot).startswith('/work'):
        sys.exit('db root must be under /work')
    EXTRA_ENV.update(dict(kv.split('=', 1) for kv in a.env))
    if a.queue_file:
        global QUEUE
        QUEUE = [tuple(x) for x in json.loads(Path(a.queue_file).read_text())]
    c = Campaign(a.run_id, dbroot, a.execute)
    if not a.execute:
        print(f'plan for {a.run_id} (db root {dbroot}):')
    sys.exit(c.run())


if __name__ == '__main__':
    main()
