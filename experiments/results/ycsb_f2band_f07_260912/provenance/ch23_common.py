"""Frozen configuration and evidence helpers for the approved Chapter 2/3 run."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import time

EXPERIMENTS = Path(__file__).resolve().parents[1]
REPO = EXPERIMENTS.parent
WORKSPACE = REPO.parent
CLEAN = WORKSPACE / 'rocksdb-f455-release' / 'db_bench'
F2 = REPO / 'db_bench'
HASHES = {
    str(CLEAN): '8b261de96ed14f84d3b652c43825ab5b2ac68cbe5ff65b95ee379633c69e883b',
    str(F2): 'c5964e54fb0afc4779fd17b82f7366bdd8d19be9bd2acae33dd26c77e55019cd',
}
GIB = 1024 ** 3
TIB = 1024 ** 4
CONFIGS = ('A_cache_zero', 'B_cache_5pct', 'C_pinned_zero', 'D_pinned_5pct')
SYSTEMS = ('baseline', 'flush_only', 'last_comp', 'fillseq', 'fillseq_ow', 'f2load')
BASE = dict(
    statistics=1, stats_interval_seconds=60, stats_per_interval=1,
    report_interval_seconds=1, enable_index_compression=False, bloom_bits=10,
    disable_wal=True, max_background_jobs=48, subcompactions=1,
    write_buffer_size=67108864, max_write_buffer_number=16,
    min_write_buffer_number_to_merge=1, batch_size=1, threads=1,
    memtablerep='vector', allow_concurrent_memtable_write=True, seed=12345678,
    use_direct_reads=True, use_direct_io_for_flush_and_compaction=True,
    compression_type='none', format_version=7, target_file_size_base=67108864,
    compaction_style=0, compaction_pri=3, num_levels=7,
    level_compaction_dynamic_level_bytes=False, max_bytes_for_level_base=268435456,
    max_bytes_for_level_multiplier=10, level0_file_num_compaction_trigger=4,
    level0_slowdown_writes_trigger=20, level0_stop_writes_trigger=36,
    soft_pending_compaction_bytes_limit=68719476736,
    hard_pending_compaction_bytes_limit=137438953472,
    disable_auto_compactions=False,
)
NO_COMPACT = dict(disable_auto_compactions=True,
    level0_file_num_compaction_trigger=1073741824,
    level0_slowdown_writes_trigger=1073741824,
    level0_stop_writes_trigger=1073741824,
    soft_pending_compaction_bytes_limit=0, hard_pending_compaction_bytes_limit=0)


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def save_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + '\n')
    tmp.replace(path)


def value_text(v):
    return str(v).lower() if isinstance(v, bool) else str(v)


def command(binary, options):
    return [str(binary)] + ['--{}={}'.format(k, value_text(v)) for k, v in options.items()]


def load_options(gib, kv, db, log, mode):
    key, value = (24, 1000) if kv == 1024 else (48, 43)
    n = gib * GIB // kv
    o = dict(BASE, num=n, key_size=key, value_size=value, db=str(db),
             report_file=str(log / 'report.rep'))
    bench = dict(baseline='fillrandom', flush_only='fillrandom',
                 fillseq='fillseq', fillseq_ow='fillseq',
                 last_comp='compact', f2load='fillvirtual')[mode]
    if mode in ('flush_only', 'last_comp'):
        o.update(NO_COMPACT)
        tail = ',flush' if mode == 'flush_only' else ''
    else:
        tail = ',flush,compact0,waitforcompaction'
    o['benchmarks'] = bench + tail + ',stats,levelstats'
    if mode == 'last_comp':
        o['use_existing_db'] = True
    if mode == 'f2load':
        # Preserve the qualified implementation's format, not clean 11.1's default.
        o['format_version'] = 6
        o.update(use_virtual_compaction=True, plr_error_bound=8, memtable_flush_size=64,
                 vcomp_register_batch_max=256, vcomp_visible_l0_batch_mb=0,
                 vcomp_log_apply_timing=False, vcomp_sort_detail_timing=False,
                 vcomp_phase1_shards=8, vcomp_materialize_workers=48)
    return o


def read_options(gib, kv, db, log, config, duration=300, threads=48, reads=10000000000,
                 seed=87654321):
    key, value = (24, 1000) if kv == 1024 else (48, 43)
    o = dict(BASE, num=gib * GIB // kv, key_size=key, value_size=value,
             db=str(db), report_file=str(log / 'report.rep'),
             use_existing_db=True, readonly=True, disable_auto_compactions=True,
             benchmarks='readrandom,stats,levelstats', read_random_exp_range=0,
             threads=threads, duration=duration, reads=reads, seed=seed,
             ops_between_duration_checks=1, cache_type='lru_cache',
             cache_size=1 if config.endswith('zero') else 50 * GIB,
             cache_index_and_filter_blocks=config.startswith(('A_', 'B_')),
             # CompactedDBImpl skips statistics for single-level states.
             # An unused merge operator selects DBImplReadOnly for every state;
             # these sources contain Put/materialized values, no Merge operands.
             merge_operator='put',
             open_files=-1, stats_level=3, histogram=True, perf_level=3,
             stats_interval_seconds=30, report_interval_seconds=10)
    return o


def ticker(text, name):
    vals = re.findall(r'^' + re.escape(name) + r' COUNT\s*:\s*(\d+)', text, re.M)
    require(vals, 'missing ticker: ' + name)
    return int(vals[-1])


def bench_stats(text, name):
    vals = re.findall(r'^' + re.escape(name) +
        r'\s+:\s+([\d.]+) micros/op\s+([\d.]+) ops/sec\s+([\d.]+) seconds\s+(\d+) operations;', text, re.M)
    require(vals, 'missing benchmark: ' + name)
    lat, throughput, seconds, ops = vals[-1]
    return dict(avg_latency_us=float(lat), throughput_ops_sec=float(throughput),
                measured_seconds=float(seconds), operations=int(ops))


def levels(text):
    require('Level Files Size(MB)' in text, 'missing levelstats')
    rows = re.findall(r'^\s*([0-6])\s+(\d+)\s+(\d+)\s*$',
                      text.split('Level Files Size(MB)')[-1], re.M)
    require(len(rows) == 7, 'incomplete final levelstats')
    return {int(level): dict(files=int(count), size_mib=int(size)) for level, count, size in rows}


def db_identity(db):
    metadata, ssts = {}, {}
    for f in sorted(Path(db).iterdir()):
        if f.name in ('CURRENT', 'IDENTITY') or f.name.startswith(('MANIFEST-', 'OPTIONS-')):
            metadata[f.name] = sha(f)
        elif f.suffix == '.sst':
            s = f.stat()
            ssts[f.name] = [s.st_ino, s.st_size, s.st_mtime_ns]
    return dict(metadata=metadata, ssts=ssts)


def stage_db(src, dst):
    src, dst = Path(src), Path(dst)
    require(not dst.exists(), 'staging destination exists: ' + str(dst))
    require((src / 'CURRENT').is_file(), 'missing source CURRENT: ' + str(src))
    before = db_identity(src)
    require(before['ssts'], 'source has no SSTs')
    dst.mkdir(parents=True)
    for f in src.iterdir():
        if f.is_file() and f.name not in ('LOG', 'LOCK') and not f.name.startswith('LOG.old.'):
            if f.suffix in ('.sst', '.blob'):
                os.link(str(f), str(dst / f.name))
            else:
                shutil.copy2(str(f), str(dst / f.name))
    require(db_identity(dst) == before, 'staged DB metadata/inode mismatch')
    return before


def snapshot(raw, suffix):
    for name in ('diskstats', 'stat', 'vmstat', 'meminfo'):
        (raw / (name + '.' + suffix)).write_text(Path('/proc/' + name).read_text())


def vm_swap(text):
    return {k: int(v) for k, v in re.findall(r'^(pswpin|pswpout) (\d+)$', text, re.M)}


# A benchmark starved of memory pages OUT, so any pswpout during a run
# invalidates it and stays a hard failure. Pages read back IN say nothing about
# our own headroom: if this benchmark needed memory the kernel would have had to
# evict something, which is pswpout. On this machine the swap traffic belongs to
# an unrelated java process holding ~81 MB, and it faults its pages back at
# arbitrary times; a 300 s cell with 52 GB resident out of 1 TB is untouched by
# that. The bound is set well above such background noise and far below anything
# that could crowd a cell, and the measured delta is recorded in every cell's
# evidence and row either way.
SWAP_IN_TOLERANCE_PAGES = 65536


def swap_delta(before_text, after_text):
    before, after = vm_swap(before_text), vm_swap(after_text)
    return {k: after.get(k, 0) - before.get(k, 0) for k in ('pswpin', 'pswpout')}


def require_no_swap_pressure(raw):
    delta = swap_delta((Path(raw) / 'vmstat.start').read_text(),
                       (Path(raw) / 'vmstat.end').read_text())
    save_json(Path(raw) / 'swap_delta.json', delta)
    require(delta['pswpout'] == 0 and delta['pswpin'] <= SWAP_IN_TOLERANCE_PAGES,
            'swap pressure during benchmark: {}'.format(delta))
    return delta


def active_benchmarks():
    pids = []
    for name in ('db_bench', 'titandb_bench'):
        result = subprocess.run(['pgrep', '-x', name], stdout=subprocess.PIPE, text=True)
        pids.extend(int(p) for p in result.stdout.split())
    return pids


def check_binary(binary):
    require(sha(binary) == HASHES[str(binary)], 'binary hash drift: ' + str(binary))


def measure(cmd, log, events, min_free=2*TIB):
    log = Path(log)
    require(not log.exists(), 'measurement directory already exists: ' + str(log))
    require(not active_benchmarks(), 'another storage benchmark is active')
    require(shutil.disk_usage('/work').free >= min_free, 'insufficient free space')
    check_binary(Path(cmd[0]))
    raw = log / 'raw'
    raw.mkdir(parents=True)
    (raw / 'command.sh').write_text('#!/usr/bin/env bash\n' + ' '.join(map(shlex.quote, cmd)) + '\n')
    save_json(raw / 'command.json', cmd)
    save_json(raw / 'binary.json', dict(path=cmd[0], sha256=HASHES[cmd[0]]))
    # Use the existing shared cache-reset policy, then require its success.
    subprocess.run(['bash', '-c', 'source "$1"; drop_page_cache', 'ch23',
                    str(EXPERIMENTS / 'lib' / 'common.sh')], check=True,
                   stdout=(raw / 'cache_reset.log').open('w'), stderr=subprocess.STDOUT)
    cache_log = (raw / 'cache_reset.log').read_text()
    require('Page cache dropped' in cache_log, 'page cache reset failed')
    snapshot(raw, 'start')
    started = time.time()
    save_json(raw / 'start.json', dict(epoch=started))
    events('BEGIN', str(log))
    monitor = None
    if shutil.which('iostat'):
        monitor = subprocess.Popen(['iostat', '-dx', '5'], stdout=(raw / 'iostat.log').open('w'))
    proc = None
    try:
        with (log / 'bench.out').open('w') as out, (raw / 'monitor.jsonl').open('w') as m:
            proc = subprocess.Popen(['/usr/bin/time', '-v', '-o', str(raw / 'time.out')] + cmd,
                                    stdout=out, stderr=subprocess.STDOUT, start_new_session=True)
            save_json(raw / 'process.json', dict(wrapper_pid=proc.pid, process_group=proc.pid))
            while True:
                try:
                    rc = proc.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    pids = active_benchmarks()
                    for pid in pids:
                        try:
                            group = os.getpgid(pid)
                        except ProcessLookupError:
                            # The benchmark can exit between pgrep and getpgid,
                            # especially at the 30-second pilot boundary.
                            continue
                        require(group == proc.pid, 'concurrent benchmark interference')
                    free = shutil.disk_usage('/work').free
                    m.write(json.dumps(dict(epoch=time.time(), free_bytes=free, benchmark_pids=pids)) + '\n')
                    m.flush()
                    require(free >= min_free, 'free space fell below safety margin')
    except BaseException:
        if proc is not None and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGINT)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=30)
        raise
    finally:
        if monitor:
            monitor.terminate()
            monitor.wait()
        snapshot(raw, 'end')
    elapsed = time.time() - started
    # Device tail is evidence only; not added to process loading time.
    subprocess.run(['sync'], check=True)
    snapshot(raw, 'after_sync')
    save_json(raw / 'execution.json', dict(exit_code=rc, elapsed_sec=elapsed,
              started_epoch=started, ended_epoch=started+elapsed))
    require(rc == 0, 'benchmark failed, exit {}: {}'.format(rc, log))
    text = (log / 'bench.out').read_text(errors='replace')
    require(not re.search(r'WARNING: (Optimization is disabled|Assertions are enabled)|Corruption:|Segmentation fault|Assertion .*failed|FATAL|Out of memory|Get returned an error:', text),
            'invalid benchmark output: ' + str(log))
    swap = require_no_swap_pressure(raw)
    rss = re.search(r'Maximum resident set size \(kbytes\):\s*(\d+)', (raw/'time.out').read_text())
    require(rss is not None, 'missing peak RSS')
    result = dict(elapsed_sec=elapsed, peak_rss_kb=int(rss.group(1)),
                  log_dir=str(log), binary_sha256=HASHES[cmd[0]],
                  swap_pages_in=swap['pswpin'])
    events('END', '{} ({:.2f}s)'.format(log, elapsed))
    return result, text


def audit_options(db, log, expected):
    options = sorted(Path(db).glob('OPTIONS-*'), key=lambda f: int(f.name.split('-')[1]))
    require(options, 'missing persisted OPTIONS')
    content = options[-1].read_text()
    shutil.copy2(str(options[-1]), str(Path(log)/'raw'/options[-1].name))
    parsed = dict(re.findall(r'^\s*([a-zA-Z_0-9]+)\s*=\s*(.*?)\s*$', content, re.M))
    for k in ('write_buffer_size', 'max_write_buffer_number', 'min_write_buffer_number_to_merge',
              'max_background_jobs', 'allow_concurrent_memtable_write', 'format_version',
              'target_file_size_base', 'num_levels', 'level_compaction_dynamic_level_bytes',
              'max_bytes_for_level_base', 'max_bytes_for_level_multiplier',
              'level0_file_num_compaction_trigger', 'level0_slowdown_writes_trigger',
              'level0_stop_writes_trigger', 'soft_pending_compaction_bytes_limit',
              'hard_pending_compaction_bytes_limit', 'disable_auto_compactions',
              'use_direct_reads', 'use_direct_io_for_flush_and_compaction', 'enable_index_compression'):
        actual, wanted = parsed.get(k), value_text(expected[k])
        if k == 'max_bytes_for_level_multiplier' and actual is not None:
            require(float(actual) == float(wanted), 'OPTIONS mismatch: ' + k)
        else:
            require(actual == wanted, 'OPTIONS {}: {} != {}'.format(k, actual, wanted))
    require(parsed.get('compaction_pri') == 'kMinOverlappingRatio', 'compaction priority mismatch')
    require(parsed.get('compaction_style') == 'kCompactionStyleLevel', 'compaction style mismatch')
    require(parsed.get('compression') == 'kNoCompression', 'compression mismatch')
    require('VectorRepFactory' in parsed.get('memtable_factory', ''), 'memtable mismatch')
    # LOG startup includes the release version and resolved block/table options.
    with (Path(db)/'LOG').open(errors='replace') as f:
        (Path(log)/'raw'/'db_LOG_startup.txt').write_text(f.read(131072))


def read_metrics(text, value_size):
    r = bench_stats(text, 'readrandom')
    ops = ticker(text, 'rocksdb.number.keys.read')
    require(r['operations'] == ops, 'aggregate benchmark/read counter mismatch')
    value_bytes = ticker(text, 'rocksdb.bytes.read')
    require(value_bytes % value_size == 0, 'non-fixed read value size')
    found = value_bytes // value_size
    hits = sum(ticker(text, 'rocksdb.'+level+'.hit') for level in ('l0', 'l1', 'l2andup'))
    require(found == hits, 'aggregate successful Get counter mismatch')
    require(0 <= found <= ops and ops > 0, 'invalid membership counts')
    negative = ticker(text, 'rocksdb.bloom.filter.useful')
    positive = ticker(text, 'rocksdb.bloom.filter.full.positive')
    true_positive = ticker(text, 'rocksdb.bloom.filter.full.true.positive')
    require(positive >= true_positive and negative + positive > 0, 'invalid filter counters')
    r.update(successful_gets=found, successful_lookup_pct=100*found/ops,
             bloom_useful=negative, bloom_full_positive=positive,
             bloom_full_true_positive=true_positive, filter_probes=negative+positive,
             filter_probes_per_op=(negative+positive)/ops,
             bloom_positive_per_op=positive/ops, bloom_true_positive_per_op=true_positive/ops,
             filter_positive_pct=100*positive/(negative+positive))
    for kind in ('filter', 'index', 'data'):
        for event in ('hit', 'miss'):
            r[kind+'_cache_'+event] = ticker(text, 'rocksdb.block.cache.'+kind+'.'+event)
    for percentile in ('50', '95', '99'):
        m = re.findall(r'^rocksdb.db.get.micros .*?P'+percentile+r'\s*:\s*([\d.]+)', text, re.M)
        require(m, 'missing Get latency histogram')
        r['p'+percentile+'_latency_us'] = float(m[-1])
    require(ticker(text, 'rocksdb.number.keys.written') == 0, 'read workload wrote keys')
    require(ticker(text, 'rocksdb.compact.write.bytes') == 0, 'read workload compacted data')
    return r
