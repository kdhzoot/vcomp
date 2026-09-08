"""Update numeric manuscript claims from the same validated rows as the figures."""
from pathlib import Path
import re


def preserve_replace(text, prefix, replacement):
    lines=text.splitlines(); found=[]; in_comment=False
    for i,line in enumerate(lines):
        if line.lstrip().startswith('%'): continue
        if r'\begin{comment}' in line: in_comment=True
        if not in_comment and line.lstrip().startswith(prefix): found.append(i)
        if r'\end{comment}' in line: in_comment=False
    if len(found)!=1:
        raise RuntimeError('Expected one active passage: {!r}, found {}'.format(prefix,len(found)))
    i=found[0]
    lines[i]='% Previous draft preserved for review (common campaign):\n% '+lines[i]+'\n'+replacement
    return '\n'.join(lines)+'\n'


def update_paper(paper, loads, reads, manifest, loading_rows, exclude_f2load=False, macros_only=False):
    b=loads['baseline_1kb']; n=loads['baseline_91b']; f=loads['flush_only_1kb']
    nf=loads['flush_only_91b']; last=loads['last_comp_1kb']; seq=loads['fillseq_1kb']
    ow=loads['fillseq_ow_1kb']; f2=None if exclude_f2load else loads['f2load_1kb']
    times={r['method']:float(r['elapsed_sec']) for r in loading_rows}
    D=lambda s: reads['D_pinned_5pct/'+s]
    q=lambda c,s: reads[c+'/'+s]['throughput_ops_sec']
    ratio=lambda c: q(c,'last_comp')/q(c,'baseline')
    macros={}
    def num(name,value,digits=1): macros['Ch'+name]=format(value,'.{}f'.format(digits))
    for name,row in [('Baseline',b),('Ninety',n),('Flush',f),('NinetyFlush',nf),
                     ('Last',last),('Seq',seq),('Overwrite',ow),('Ftwo',f2)]:
        if row is None:
            continue
        num(name+'Min',row['elapsed_sec']/60)
        num(name+'Sec',row['elapsed_sec'])
        num(name+'Hour',row['elapsed_sec']/3600,2)
    num('FlushSpeedup',b['elapsed_sec']/f['elapsed_sec'],2)
    num('NinetyFlushSpeedup',n['elapsed_sec']/nf['elapsed_sec'],2)
    num('SeqSpeedup',b['elapsed_sec']/seq['elapsed_sec'],2)
    num('OverwriteSpeedup',b['elapsed_sec']/ow['elapsed_sec'],2)
    if f2 is not None:
        num('FtwoSpeedup',b['elapsed_sec']/f2['elapsed_sec'],2)
    num('AdocMin',times['ADOC']/60)
    num('AdocRatio',times['ADOC']/b['elapsed_sec'],2)
    num('BlobMin',times['BlobDB (GC off)']/60)
    num('BlobSpeedup',b['elapsed_sec']/times['BlobDB (GC off)'],2)
    num('LastRatio',last['elapsed_sec']/b['elapsed_sec'],2)
    num('LastTailMin',last['phases'][0]['elapsed_sec']/60)
    num('LastReadGiB',last['compaction_read_bytes']/1024**3)
    num('LastWriteGiB',last['compaction_sst_write_bytes']/1024**3)
    num('BaselineWaf',b['waf'],2)
    num('BaselineDeviceWaf',b['device_waf'],2)
    if f2 is not None:
        num('FtwoDeviceWaf',f2['device_waf'],2)
        num('DiskWriteReduction',100*(1-f2['device_write_bytes']/b['device_write_bytes']),1)
        num('DiskWriteRatio',b['device_write_bytes']/f2['device_write_bytes'],2)
    num('BaselineStall',b['stall_sec'],0)
    num('BaselineCompJobs',b['compaction_jobs'],0)
    num('BaselineCompReadMiB',b['compaction_read_bytes']/max(1,b['compaction_jobs'])/1024**2)
    num('BaselineCompDuration',b['cumulative_compaction_sec']/max(1,b['compaction_jobs']),3)
    num('SeqDeviceWaf',seq['device_waf'],2)
    num('CreatedSst',b['created_sst_count'],0)
    num('FinalSst',b['final_sst_count'],0)
    num('RetainedSstPct',b['retained_sst_pct'],2)
    num('FlushSst',f['final_sst_count'],0)
    num('LastSst',last['final_sst_count'],0)
    for system,name in [('baseline','Baseline'),('flush_only','Flush'),('last_comp','Last'),('f2load','Ftwo')]:
        if exclude_f2load and system == 'f2load':
            continue
        row=D(system)
        num(name+'Checks',row['filter_probes_per_op'],2)
        num(name+'PositivePct',row['filter_positive_pct'],2)
        num(name+'PositivePerOp',row['bloom_positive_per_op'],2)
        num(name+'FoundPct',row['successful_lookup_pct'],2)
    if not exclude_f2load:
        num('FtwoMembershipDelta',D('f2load')['successful_lookup_pct']-D('baseline')['successful_lookup_pct'],2)
    for config,tag in [('A_cache_zero','CachedZero'),('C_pinned_zero','PinnedZero'),('D_pinned_5pct','PinnedFifty')]:
        num(tag+'BaselineQps',q(config,'baseline'),0)
        num(tag+'FlushQps',q(config,'flush_only'),0)
        num(tag+'FlushSlowdown',q(config,'baseline')/q(config,'flush_only'),1)
    num('LastReadMinRatio',min(ratio(c) for c in ('A_cache_zero','B_cache_5pct','C_pinned_zero','D_pinned_5pct')),2)
    num('LastReadMaxRatio',max(ratio(c) for c in ('A_cache_zero','B_cache_5pct','C_pinned_zero','D_pinned_5pct')),2)
    macro_path=paper/'tex'/'ch23_measurements.tex'
    macro_path.write_text('% Generated from '+manifest['run_id']+'; do not edit numbers independently.\n'+
        '\n'.join(r'\newcommand{\%s}{%s}'%(k,v) for k,v in sorted(macros.items()))+'\n')
    if macros_only:
        return
    bgpath=paper/'tex'/'02_Background.tex'; text=bgpath.read_text()
    text=r'\input{tex/ch23_measurements.tex}'+'\n'+text
    changes=[
      (r'\caption{Loading time and WAF by DB size.}',
       r'        \caption{Historical scaling; diamonds: common baseline.}'),
      (r'\caption{Cumulative breakdown of background jobs.}',
       r'        \caption{Historical background-job breakdown.}'),
      (r'\caption{Dataset loading cost.',
       r'    \caption{Dataset loading cost; sizes denote logical input bytes. (a) Bars and WAF lines retain the historical scaling configuration; diamonds show the new common-release baseline at 1,000~GiB, also used in (b). (b) Single-run common-release measurements with 16 write buffers; the 1~KB Conventional and Flush-only results are shared with Figure~\ref{fig:bg-compaction-alternatives}. WAF is total flush/compaction SST bytes divided by logical input bytes. (c) Separate historical instrumented measurements at 1,000~GiB with two write buffers: Flush comes from flush-only, Compaction from conventional loading. Its cumulative job times are not the wall times in (b).}'),
      ('Conventional loading으로 TB급 규모의 데이터셋을 구축하는 것은',
       r'Conventional loading의 비용을 비교할 때는 logical input, build와 종료 조건을 함께 고정해야 한다. Figure~\ref{fig:bg-loading-flush-only}와 Figure~\ref{fig:bg-compaction-alternatives}의 공통 baseline은 RocksDB 11.1.0 revision \texttt{f455ab7b}의 동일 release 실행 파일을 사용한다. Assertions는 비활성화하고 INFO logging, Vector memtable, 64~MiB write buffer 최대 16개, 64~MiB target SST, format version 7, static leveled compaction과 \texttt{kMinOverlappingRatio}, background jobs 48개, subcompaction 1개를 고정하였다. WAL과 compression은 비활성화하고 direct I/O를 사용한다. Logical input 1,000~GiB는 최종 DB 크기가 아니라 제출한 key와 value byte의 합이며, 1~KB는 24~B key와 1,000~B value, 91~B는 48~B key와 43~B value이다. One writer와 batch size 1, seed 12345678로 random writes with replacement를 수행하고 최종 flush와 compaction drain까지 측정하였다. 이 조건의 conventional loading은 각각 \ChBaselineHour{}시간과 \ChNinetyHour{}시간이 걸렸다.\n\nFigure~\ref{fig:bg-loading-scale}의 기존 scaling bar와 WAF line은 assertions가 활성화된 clean build, write buffer 2개를 사용한 별도 실행이다. 해당 1~KB 계열은 SkipList, 91~B 계열은 Vector memtable을 사용하므로 새 common baseline 계열과 연결하지 않는다. 1,000~GiB의 새 공통 측정은 별도 마름모로 표시하였다. 기존 계열의 8,000~GiB 로딩은 1~KB에서 13.25시간, 91~B에서 46.23시간이었다. 이 historical scaling 결과와 아래 instrumented breakdown의 재측정은 별도 과제이며, 새로운 release baseline의 비용이나 배율로 해석하지 않는다.'),
      ('flush-only에서는 compaction이 활성화됐을 때보다',
       r'공통 release 실험에서 flush-only는 conventional loading보다 1~KB와 91~B에서 각각 \ChFlushSpeedup{}$\times$와 \ChNinetyFlushSpeedup{}$\times$ 빠르다(Figure~\ref{fig:bg-loading-flush-only}). Flush-only는 automatic compaction과 L0/pending-byte stall 제한을 해제하고 마지막 flush까지만 수행한다. Build, 입력과 나머지 공통 설정은 conventional loading과 같다. Figure~\ref{fig:bg-loading-breakdown}의 별도 historical 계측은 compaction을 제거해도 sorting, SST build와 write I/O 비용이 남음을 보여준다.'),
      (r'Figure~\ref{fig:bg-loading-breakdown}의 Flush breakdown은',
       r'Figure~\ref{fig:bg-loading-breakdown}는 RocksDB 10.10.1 기반 instrumented release build, Vector memtable, 64~MiB write buffer 2개, format version 6에서 측정한 historical 결과이다. 그 외 입력 크기, KV 구성, seed, one writer, background jobs 48개와 subcompaction 1개는 위 공통 실험과 같다. Flush breakdown은 compaction과의 resource contention을 배제하기 위해 flush-only에서 측정하였다. 이 계측 실행 안에서 conventional loading의 cumulative flush time은 flush-only보다 1~KB에서 30.2\%, 91~B에서 3.0\% 높았다. 이 비율은 16-buffer common-release 실험의 측정값이 아니며, 누적 background-job elapsed time은 loading wall time이나 CPU time과 구분한다.'),
      ('그러나 개별 compaction에서 발생하는 CPU 및 I/O 비용보다',
       r'동일한 데이터가 여러 compaction에 반복적으로 참여한다는 점은 개별 job 비용을 누적시킨다. Flush로 기록된 L0 SST는 이후 여러 level의 compaction에서 다시 읽히고 새로운 SST로 기록된다. Figure~\ref{fig:bg-loading-flush-only}의 SST WAF는 전체 flush/compaction SST write bytes를 logical KV input bytes로 나눈 값이며, 별도로 보고하는 device-level WAF는 storage device의 write counter를 같은 입력량으로 나눈 값이다. 공통 1~KB Conventional의 SST WAF는 \ChBaselineWaf{}이다. 두 지표를 혼용하지 않으며, 반복적인 SST rewriting은 write I/O뿐 아니라 merge와 SST build에 필요한 CPU 비용도 누적시킨다.'),
      ('데이터셋의 규모가 증가하면 LSM-Tree는 더 많은 레벨을 형성하고,',
       r'Figure~\ref{fig:bg-loading-scale}의 historical 계열에서는 logical input이 500~GiB에서 8,000~GiB로 증가할 때 SST WAF가 1~KB에서 12.4에서 20.8로, 91~B에서 13.5에서 22.1로 증가하였다. 이 계열은 규모에 따른 반복 rewriting 비용을 보여주지만, common-release 실험의 크기 확장 결과로 사용하지 않는다.'),
      (r'\caption{Single-run settled loading time for a 1~TB',
       r'    \caption{Single-run loading time for a 1,000~GiB logical input domain with 1~KB KV pairs. Fillseq+OW submits an additional 10\% writes. Baseline, Flush-only, Last-comp, Fillseq and Fillseq+OW use the same clean release executable; BlobDB reuses a validated result from that executable. ADOC and \name{} use their method-specific implementations. Each read state in Figure~\ref{fig:bg-alternative-reads} is the DB from the corresponding loading result. Lower is better.}'),
      ('앞 절에서는 conventional loading의 주요 병목이',
       r'앞 절에서는 conventional loading의 주요 비용이 intermediate SST를 반복해서 생성하고 compaction하는 작업임을 살펴보았다. 본 절에서는 각 대안의 loading time뿐 아니라 그 결과로 만들어진 LSM-Tree state가 subsequent point lookup의 동작을 보존하는지를 함께 살펴본다. Figure~\ref{fig:bg-naive-lsm-states}는 각 state의 개념도를, Figure~\ref{fig:bg-compaction-alternatives}는 1,000~GiB logical input domain에서의 loading time을 보여준다.'),
      (r'Figure~\ref{fig:bg-compaction-alternatives}의 Baseline, ADOC, BlobDB는',
       r'Figure~\ref{fig:bg-compaction-alternatives}의 Baseline, ADOC, BlobDB는 24~B key와 1,000~B value로 구성된 1,000~GiB logical input을 loading하고 terminal flush와 compaction이 끝날 때까지 측정한 결과이다. One foreground writer, Vector memtable, direct I/O, disabled WAL and compression, 48 background jobs와 16 write buffers를 공통으로 사용한다. Baseline은 \ChBaselineMin{}분, ADOC은 \ChAdocMin{}분으로 Baseline의 \ChAdocRatio{}배의 시간이 걸렸고, BlobDB는 \ChBlobMin{}분으로 \ChBlobSpeedup{}$\times$ 빨랐다. Baseline과 BlobDB는 동일 clean release 실행 파일을 사용하며, BlobDB는 이미 검증된 해당 build의 결과를 재사용하였다. ADOC은 저자 artifact의 기존 검증 결과로, 이 비교는 ADOC에 대한 version-matched causal comparison은 아니다.'),
      ('ADOC은 compaction work를 제거하지 않고',
       r'ADOC은 compaction work를 제거하지 않고 그 granularity와 scheduling을 조절한다. 공통 Baseline의 device-level WAF는 \ChBaselineDeviceWaf{}, compaction job 수는 \ChBaselineCompJobs{}, job당 평균 read size는 \ChBaselineCompReadMiB{}~MiB, 평균 duration은 \ChBaselineCompDuration{}초, cumulative stall time은 \ChBaselineStall{}초였다. 재사용한 ADOC 실행의 대응 값은 각각 11.16, 19,059개, 530.5~MiB, 1.328초와 3,002초였다. ADOC에서는 pending-compaction hard stop이 10,196회 기록되었고, thread 수는 모든 관측 구간에서 이미 상한 48이었으며 batch size는 약 53\%의 구간에서 최대값 512~MiB였다. 이 결과는 scheduling 조절로 intermediate compaction 자체가 없어지지는 않으며, 그 효과를 job 크기와 stall 및 최종 loading time과 함께 평가해야 함을 보여준다.'),
      ('BlobDB는 모든 1,000~B value를 blob file로 분리하여',
       r'BlobDB는 모든 1,000~B value를 blob file로 분리하여 compaction이 key와 reference만 처리하도록 하였다. 보존된 실행에서 compaction 중 blob read는 0~B, device-level WAF는 1.50, cumulative stall time은 610초였다. 새 공통 Baseline에 대한 loading speedup은 \ChBlobSpeedup{}$\times$이다. 이 결과는 garbage collection (GC)을 비활성화한 initial loading이며 완료 시 약 338~GB의 unreferenced blob과 1.5$\times$ blob space amplification을 남겼다. 이후 GC는 valid blob을 다시 읽고 쓰며 point lookup은 LSM lookup 뒤에 blob access를 추가한다. 따라서 BlobDB는 반복해서 이동하는 payload를 줄이지만, key/reference를 담은 intermediate SST의 flush와 compaction은 계속 수행한다.'),
      ('이 두 접근의 한계는 데이터셋 로딩에만 존재하는 기회를',
       r'Conventional loading 중 만들어진 intermediate SST는 compaction decision을 통해 final layout을 형성하는 데 필요하지만, loading이 끝나기 전에 subsequent workload가 그 payload를 관찰하지는 않는다. 이번 공통 Baseline에서 생성된 \ChCreatedSst{}개의 SST 중 final state에 남은 파일은 \ChFinalSst{}개, 즉 \ChRetainedSstPct{}\%였다. 필요한 것은 intermediate compaction의 결정을 없애는 것이 아니라, 곧 대체될 SST의 KV data를 물리적으로 materialize하고 다시 읽고 쓰는 비용을 제거하는 것이다.'),
      ('Intermediate compaction의 physical data movement가 불필요하다면',
       r'Loading 동안 compaction을 끄는 Flush-only는 \ChFlushMin{}분으로 Baseline보다 \ChFlushSpeedup{}$\times$ 빠르다. 이 값과 원본 DB는 Figure~\ref{fig:bg-loading-flush-only} 및 Figure~\ref{fig:bg-alternative-reads}와 공유한다. 완료 시 \ChFlushSst{}개의 SST가 모두 L0에 남고 다른 level은 비어 있었다. 서로 overlapping key range를 갖는 다수의 L0 SST를 확인하는 lookup은 여러 non-L0 level의 non-overlapping SST를 사용하는 conventional state와 다른 access pattern을 만든다.'),
      ('Flush-only의 L0 overlap을 없애기 위해 loading 후',
       r'Flush-only의 L0 overlap을 없애기 위해 그 DB의 checkpoint에서 전체 key range를 한 번에 compaction하였다. One subcompaction으로 수행한 이 방법은 \ChFlushMin{}분의 공유 Flush-only prefix에 \ChLastTailMin{}분의 단일 compaction을 추가하여 총 \ChLastMin{}분, Baseline의 \ChLastRatio{}배의 시간이 걸렸다. Checkpoint 준비 시간은 이 두 측정 구간에서 제외하였다. 마지막 job은 \ChLastReadGiB{}~GiB를 읽고 \ChLastWriteGiB{}~GiB를 썼으며, 결과는 \ChLastSst{}개의 non-overlapping SST를 하나의 non-L0 level에만 배치하였다. 따라서 lookup이 확인할 candidate SST는 최대 하나로 줄고, 나머지 level이 비어 있어 subsequent write의 compaction path도 conventional state와 달라진다.'),
      ('여러 level을 적은 비용으로 채우는 또 다른 방법은',
       r'Key를 순서대로 삽입하는 \textit{fillseq}에서는 새 SST가 기존 SST와 겹치지 않을 때 data를 읽고 다시 쓰는 대신 level metadata만 변경하는 trivial move를 사용할 수 있다~\cite{rocksdb_trivial_move}. 공통 release 설정의 Fillseq는 \ChSeqMin{}분으로 Baseline보다 \ChSeqSpeedup{}$\times$ 빨랐고, device-level WAF는 \ChSeqDeviceWaf{}였다. 여러 level에 SST가 존재하더라도 아래에서 설명하는 inter-level overlap과 key membership까지 보존되는 것은 아니다.'),
      ('이 구조를 conventional state에 가깝게 만들기 위해 fillseq 이후',
       r'Fillseq 이후 동일한 key space에 record count의 10\%에 해당하는 104,857,600회 random overwrite를 수행하였다. 전체 순차 입력과 overwrite 후의 flush/compaction drain을 각각 포함한 시간은 \ChOverwriteMin{}분으로, Baseline에 대한 speedup은 \ChOverwriteSpeedup{}$\times$이다. 이 경우 domain은 1,000~GiB이지만 총 logical write volume은 1,100~GiB이다. Overwrite는 replacement sampling이므로 서로 다른 key의 정확히 10\%를 바꾸지는 않는다. Cross-level overlap은 일부 복원할 수 있어도 이미 모든 key가 존재하는 logical key set은 바뀌지 않으며, random-load state의 negative lookup을 재현하지 못한다.'),
      (r'\caption{Read-side consequences of the alternative loading states',
       r'    \caption{Read-side consequences of the loading states in Figure~\ref{fig:bg-compaction-alternatives}, under uniform point reads with 48 threads and direct reads over the same 1,000~GiB key domain. All readers use the common clean release executable. (a) Filter checks per lookup (bars, left axis capped at 5; clipped bars show their actual values) and Filter positive as a percentage of all checks (diamonds, right axis). (b) Successful-lookup fraction from aggregate Get counters. Panels (a)/(b) use pinned metadata and 50~GiB cache. (c) Throughput divided by Baseline in the same metadata/cache configuration; red marks 1. Each cell is one 300-second run; $\approx$0 means a one-byte LRU. Pinned metadata uses additional memory outside the stated cache.}'),
      ('Final state가 subsequent read behavior를 어떻게 바꾸는지',
       r'Figure~\ref{fig:bg-compaction-alternatives}의 각 loading 결과가 만든 바로 그 DB에서 read experiment를 수행하였다. 모든 reader는 Baseline 로딩에 사용한 동일 RocksDB 11.1.0 release 실행 파일의 \texttt{readrandom}을 사용한다. Seed 87654321, uniform request distribution, 48 threads, 300초, direct read를 고정하고 automatic compaction을 비활성화하였다. 원본 SST를 hard-link하고 mutable metadata를 복사한 staged DB를 read-only로 열어 원본을 보존하였다. 모든 state가 일반 read-only 경로와 동일한 통계 수집을 사용하도록, Merge operand가 없는 데이터에 사용되지 않는 \texttt{put} merge operator를 설정하여 single-level 전용 fast path를 비활성화하였다. Filter/index를 cache에 넣거나 open table reader가 유지하도록 하고 LRU 용량은 1~B 또는 50~GiB로 설정하였다. 별도 warm-up 없이 DB open 이후 300초를 측정하였고 각 실행 전 page cache를 초기화하였다. \name{}도 \ChFtwoSec{}초의 새 로딩 결과와 그 DB를 함께 사용하므로 별도의 loading/read 재구축 결과를 혼합하지 않는다.'),
      ('Conventional loading은 request당 3.62개의 filter를',
       r'공통 Baseline은 request당 \ChBaselineChecks{}개의 filter를 확인하며, 그중 \ChBaselinePositivePct{}\%가 Filter positive이다. Flush-only는 request당 \ChFlushChecks{}개의 filter를 확인하고 Filter positive 비율은 \ChFlushPositivePct{}\%로, request당 \ChFlushPositivePerOp{}개의 candidate SST가 filter를 통과한다. 그림의 왼쪽 축은 5에서 잘라 실제 초과 값을 막대 위에 표시하였다. Last-comp는 \ChLastChecks{} checks와 \ChLastPositivePct{}\%의 Filter positive 비율을 보이며, \name{}의 대응 값은 \ChFtwoChecks{}와 \ChFtwoPositivePct{}\%이다. 짧은 lookup path만으로 fidelity를 판단할 수 없으며, single-level state의 candidate 축소와 conventional state의 여러 level에 걸친 lookup을 구분해야 한다.'),
      ('Lookup membership에서는 또 다른 차이가 드러난다',
       r'Lookup membership에서도 차이가 드러난다(Figure~\ref{fig:bg-alternative-lookup-hits}). Baseline, Flush-only, Last-comp의 successful-lookup fraction은 각각 \ChBaselineFoundPct{}\%, \ChFlushFoundPct{}\%, \ChLastFoundPct{}\%이다. 분자는 전체 스레드가 성공적으로 읽은 value bytes와 level별 Get-hit counter로 교차 검증하고, 분모는 전체 Get 수를 사용하였다. Fillseq와 Fillseq+OW는 전체 domain을 먼저 삽입하므로 모든 key가 존재하며, 이후 overwrite도 negative lookup을 복원하지 못한다. 따라서 lookup work 감소와 key membership의 변경을 함께 살펴보아야 한다.'),
      (r'\name{}은 동일한 nominal operation count와 key domain을',
       r'\name{}은 동일 nominal input count와 key domain을 사용하지만, metadata-only path는 원래 key identity를 보존하는 대신 KMV sketch와 learned-index descriptor로 key set을 근사 재구성한다. 이번 successful-lookup fraction은 \ChFtwoFoundPct{}\%이며 Baseline 대비 차이는 \ChFtwoMembershipDelta{} percentage points이다. 이러한 membership 차이는 current prototype의 key-set fidelity 한계이며, throughput을 동일 key set에서의 controlled speedup으로 해석할 수 없게 한다.'),
      ('이러한 structural difference는 cache를 추가해도 사라지지 않는다',
       r'Cache 조건별 throughput을 비교하면, cached metadata와 1~B LRU에서 Baseline은 \ChCachedZeroBaselineQps{} ops/s, Flush-only는 \ChCachedZeroFlushQps{} ops/s였다. Pinned metadata와 1~B LRU에서는 각각 \ChPinnedZeroBaselineQps{}와 \ChPinnedZeroFlushQps{} ops/s이며, pinned metadata와 50~GiB LRU에서는 \ChPinnedFiftyBaselineQps{}와 \ChPinnedFiftyFlushQps{} ops/s였다. 마지막 조건에서도 Baseline/Flush-only throughput 비율은 \ChPinnedFiftyFlushSlowdown{}$\times$이다. Metadata와 data caching은 개별 access cost를 줄이지만 많은 candidate filter를 확인하는 구조 자체를 바꾸지는 않는다. Last-comp의 Baseline 대비 throughput은 네 조건에서 \ChLastReadMinRatio{}--\ChLastReadMaxRatio{}배였으며, 그 single-level state의 단순화와 함께 해석해야 한다.'),
      (r'\name{}은 네 configuration에서 Baseline과 같은 throughput order를',
       r'\name{}의 throughput은 Figure~\ref{fig:bg-alternative-read-throughput}에 동일 cache 조건의 Baseline으로 정규화하였다. Key membership과 lookup hit mix가 다르므로 이 값은 contextual evidence이며 controlled layout-only speedup은 아니다. 모든 cell은 한 번 측정했고, configuration별 state 실행 순서는 사전에 정하여 정방향과 역방향을 교대로 사용하였다. 본 실험은 read-only lookup consequence를 측정하며 subsequent write의 compaction cost를 직접 측정하지 않는다. Settled loading time이나 level 수뿐 아니라 candidate-SST composition과 live-key membership도 final-state fidelity의 평가 대상이어야 한다.'),
    ]
    if exclude_f2load:
        omitted = r'\name{} is omitted pending validation under the common protocol.'
        revised = []
        for prefix, replacement in changes:
            if prefix.startswith(r'\caption{Single-run settled'):
                replacement = replacement.replace(
                    r'ADOC and \name{} use their method-specific implementations.',
                    'ADOC uses its author artifact. ' + omitted)
            elif prefix.startswith(r'\caption{Read-side consequences'):
                replacement = replacement[:-1] + ' ' + omitted + '}'
            elif prefix.startswith('Final state가 subsequent'):
                replacement = replacement.replace(
                    r'\name{}도 \ChFtwoSec{}초의 새 로딩 결과와 그 DB를 함께 사용하므로 별도의 loading/read 재구축 결과를 혼합하지 않는다.',
                    r'검증된 다섯 loading state와 각각의 원본 DB만 사용하며, 보류된 \name{}의 기존 loading/read 결과는 이 공통 비교에 포함하지 않는다.')
            elif prefix.startswith('Conventional loading은 request당'):
                replacement = replacement.replace(
                    r'Last-comp는 \ChLastChecks{} checks와 \ChLastPositivePct{}\%의 Filter positive 비율을 보이며, \name{}의 대응 값은 \ChFtwoChecks{}와 \ChFtwoPositivePct{}\%이다.',
                    r'Last-comp는 \ChLastChecks{} checks와 \ChLastPositivePct{}\%의 Filter positive 비율을 보인다.')
            elif prefix.startswith(r'\name{}은 동일한 nominal'):
                replacement = (r'\name{}의 metadata-only path는 원래 key identity를 보존하는 대신 KMV sketch와 learned-index descriptor로 key set을 근사 재구성한다. '
                    r'\TBC{공통 종료 조건을 통과한 \name{}의 loading 결과와 동일 DB에서 수행한 네 read 결과를 확보한 뒤, key-set fidelity와 lookup membership 차이를 이 비교에 추가한다.} '
                    r'이전 실행의 membership 수치를 새 common-baseline 결과에 대한 측정으로 사용하지 않는다.')
            elif prefix.startswith(r'\name{}은 네 configuration'):
                replacement = (r'Figure~\ref{fig:bg-alternative-read-throughput}는 각 state의 throughput을 동일 cache 조건의 Baseline으로 정규화한다. '
                    r'Key membership과 lookup hit mix가 다르므로 이 값은 controlled layout-only speedup이 아니다. '
                    r'모든 cell은 한 번 측정했고 configuration별 state 실행 순서는 사전에 정한 정방향과 역방향 순서를 교대로 사용하였다. '
                    r'본 실험은 read-only lookup consequence를 측정하며 subsequent write의 compaction cost를 직접 측정하지 않는다. '
                    r'Loading time이나 level 수뿐 아니라 candidate-SST composition과 live-key membership도 final-state fidelity의 평가 대상이어야 한다.')
            revised.append((prefix, replacement))
        changes = revised
        changes.append(('즉 practical loader는 natural accumulation을 단순히',
            r'즉 practical loader는 natural accumulation을 단순히 우회하거나 그 endpoint를 추측해서는 안 된다. 대신 final state를 형성하는 compaction decision path는 보존하되, 그 결정을 실행하기 위한 intermediate KV movement만 제거해야 한다. Section~\ref{sec:design}에서 소개하는 \name{}은 natural loading path를 compact in-memory SST descriptor와 metadata operation으로 simulation하고 final state가 확정된 후 실제 KV data를 한 번만 materialize함으로써 이 세 요구사항을 함께 목표로 한다. 다만 approximate materialization의 exact live-key membership 보존 여부는 별도 검증 대상이며, 보류된 \name{}의 공통 read 결과를 포함하지 않는 Figure~\ref{fig:bg-alternative-lookup-hits}로 그 fidelity를 입증하지 않는다.'))
    for prefix,replacement in changes:
        text=preserve_replace(text,prefix,replacement.replace('\\n\\n','\n\n'))
    bgpath.write_text(text)
    # The author deferred the scaling campaign, so retain its old figure and
    # identify it explicitly; use the new shared 1,000-GiB comparison for current
    # headline claims instead of repeating an obsolete baseline elsewhere.
    ep=paper/'tex'/'04_Evaluation.tex'; et=ep.read_text()
    et=preserve_replace(et,r'\caption{Loading time of the baseline and',
       r'    \caption{Historical loading-time scaling series, retained separately from the common-release comparisons in Figures~\ref{fig:bg-loading-flush-only} and~\ref{fig:bg-compaction-alternatives}. Its original projected points remain hatched. These bars are not a scaling series measured with the new 16-buffer release baseline.}')
    et=preserve_replace(et,'We run all experiments on a server with',
       r'Experiments use the reference server with Intel Xeon Gold 6336Y CPUs, approximately 1~TiB DRAM and 31 Samsung PM9A3 NVMe SSDs~\cite{samsung_pm9a3_datasheet} in RAID-0. \name{} is implemented on RocksDB 10.10.1. The common comparisons in Sections~\ref{sec:background-loading-bottleneck} and~\ref{sec:alternatives} use the explicitly recorded clean RocksDB 11.1.0 release baseline and 16 write buffers; method-specific builds are disclosed there. The historical scaling figure below retains its earlier builds and configurations and must not be read as sharing that baseline. Leveled compaction, kMinOverlappingRatio~\cite{rocksdb_leveled}, nominal 64~MiB buffers/SST targets, no compression and up to 48 background jobs are common exposed settings, not a claim of identical builds across historical experiments.')
    et=preserve_replace(et,'To measure how much',
       r'The historical Figure~\ref{fig:eval-speedup} retains its original size-scaling measurements and projections. Repeating that complete matrix with the common release baseline is deferred. The newly measured common 1,000~GiB, 1~KB comparison instead uses the same input settings and the exact source DBs as Figures~\ref{fig:bg-compaction-alternatives} and~\ref{fig:bg-alternative-reads}.')
    if exclude_f2load:
        historical = next(line for line in et.splitlines()
                          if line.startswith(r'Figure~\ref{fig:eval-speedup} shows that baseline loading time'))
        historical = historical.replace(r'Figure~\ref{fig:eval-speedup} shows',
            r'The separately configured historical series in Figure~\ref{fig:eval-speedup} shows', 1)
        et=preserve_replace(et,r'Figure~\ref{fig:eval-speedup} shows that baseline loading time',
            historical + '\n\n' + r'The new common-release 1,000~GiB conventional results are \ChBaselineSec{} seconds for 1~KB and \ChNinetySec{} seconds for 91~B. '
            r'They use the settings in Section~\ref{sec:background-loading-bottleneck} and are distinct from the historical series above. '
            r'\TBC{The corresponding \name{} loading/read results remain deferred after failing the common completion check; no new speedup against these baselines is reported.}')
        et=preserve_replace(et,'This speedup comes from eliminating the CPU and I/O cost',
            r'The common 1,000~GiB, 1~KB baseline writes \ChBaselineWaf{} times the logical input volume as flush/compaction SST bytes. '
            r'Its separately measured device-write amplification is \ChBaselineDeviceWaf{}. Device counters include storage writes beyond SST payloads and are kept separate from SST WAF. '
            r'These new baseline measurements do not establish a common-protocol write-reduction ratio for \name{} while its validation remains deferred.')
    else:
        et=preserve_replace(et,r'Figure~\ref{fig:eval-speedup} shows that baseline loading time',
           r'In the new common 1,000~GiB comparison, conventional loading takes \ChBaselineSec{} seconds (\ChBaselineMin{} minutes) and \name{} takes \ChFtwoSec{} seconds, a \ChFtwoSpeedup{}$\times$ loading-time ratio. These values are shared with Figure~\ref{fig:bg-compaction-alternatives}; they do not replace one point inside the differently configured historical scaling series. The historical figure retains its larger-scale timings and projected points, which are not used to compute the new ratio.')
        et=preserve_replace(et,'This speedup comes from eliminating the CPU and I/O cost',
           r'The common baseline writes \ChBaselineWaf{} times the logical input volume as flush/compaction SST bytes. Its separately measured device-write amplification is \ChBaselineDeviceWaf{}, versus \ChFtwoDeviceWaf{} for \name{}, a \ChDiskWriteReduction{}\% reduction in device writes in this 1,000~GiB comparison. Device counters include storage writes beyond SST payloads and are kept separate from SST WAF. \name{} avoids intermediate materialization and the associated KV merging and table rebuilding, then materializes the final vSSTs once.')
    ep.write_text(et)
    for name in ('00_Abstract.tex','01_Introduction.tex','05_Conclusion.tex'):
        path=paper/'tex'/name; original=path.read_text(); lines=original.splitlines(); out=[]
        for line in lines:
            new=line
            if not line.lstrip().startswith('%'):
                new=new.replace(r'reduces loading time by up to 47.9$\times$ over natural accumulation',
                    r'reduces loading time by \ChFtwoSpeedup{}$\times$ in the common 1,000~GiB, 1~KB comparison')
                new=new.replace(r'reduces loading time by up to 47.9$\times$ and total disk write by up to 96.6\%',
                    r'reduces loading time by \ChFtwoSpeedup{}$\times$ and device writes by \ChDiskWriteReduction{}\% in the common 1,000~GiB, 1~KB comparison')
                new=new.replace('기존 natural loading 대비 최대 47.9배의 speedup을 달성하고',
                    r'공통 1,000~GiB, 1~KB 비교에서 \ChFtwoSpeedup{}배의 loading speedup을 달성하고')
                new=new.replace(r'total disk write를 최대 96.6~\% 감소시켰다',
                    r'device write를 \ChDiskWriteReduction{}~\% 감소시켰다')
                new=new.replace('특히 기존 방식으로 약 47시간이 필요할 것으로 예상되는',
                    'Historical scaling 결과에서는 기존 방식으로 약 47시간이 필요할 것으로 예상되는')
                new=new.replace('In particular, it builds an 8~TB dataset',
                    'In the separate historical scaling series, it builds an 8~TB dataset')
                new=new.replace(r'It builds an 8\,TB dataset',r'The separate historical scaling series builds an 8\,TB dataset')
                if exclude_f2load:
                    # Keep these historical claims tied to their original data;
                    # never synthesize a new F2Load ratio from the fresh baseline.
                    new=line.replace(r'reduces loading time by up to 47.9$\times$ over natural accumulation',
                        r'reduces loading time by up to 47.9$\times$ over natural accumulation in the separate historical scaling series')
                    new=new.replace(r'On RocksDB, \name{} reduces loading time',
                        r'In the separate historical RocksDB scaling series, \name{} reduces loading time')
                    new=new.replace('Loading performance 평가에서', '별도 historical scaling의 loading performance 평가에서')
            if new!=line:
                out.extend(['% Previous draft preserved for review (common campaign):','% '+line,new])
            else: out.append(line)
        rendered='\n'.join(out)+'\n'
        if name=='01_Introduction.tex':
            rendered=preserve_replace(rendered,'그러나 Compaction은 CPU의 merge sorting과',
               r'Compaction은 CPU의 merge sorting과 disk I/O를 함께 요구한다. Foreground write와 background job은 자원을 공유하고, compaction이 누적되면 write stall이 발생할 수 있다. 공통 1,000~GiB, 1~KB baseline에서 flush와 compaction의 전체 SST write volume은 logical input의 \ChBaselineWaf{}배였다. 별도 historical scaling 계열의 SST WAF는 500~GiB에서 12.4, 8,000~GiB에서 20.8이었다(Figure~\ref{fig:bg-loading-scale}). 이 수치는 SST WAF이며 device-level write amplification과 구분한다.')
        path.write_text(rendered)
    # Macros are also used before Chapter 2 (abstract/introduction).
    bgpath.write_text(bgpath.read_text().replace(r'\input{tex/ch23_measurements.tex}'+'\n','',1))
    main=paper/'main.tex'; mt=main.read_text()
    if r'\input{tex/ch23_measurements.tex}' not in mt:
        mt=mt.replace(r'\begin{document}',r'\input{tex/ch23_measurements.tex}'+'\n'+r'\begin{document}',1)
    main.write_text(mt)
