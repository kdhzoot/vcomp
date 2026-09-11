#!/usr/bin/env python3
"""Selected fidelity metrics for the completed September 10-11 YCSB repeats.

Use the existing campaign exclusion policy, retain all individual observations,
and distinguish copied source states from newly materialized F2Load states.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import statistics as stats

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.lines import Line2D
from matplotlib.patches import Patch
from matplotlib.ticker import PercentFormatter

from compare_five_baseline_ycsb import EXP, LETTERS, RESULTS, command_options, read_cells, tsv
from plot_baseline_band import ORDER, EXCLUDE
from plot_ycsb_raw_metrics import DEFINITIONS, GROUPS, GIB, format_value, get_metrics, style

RUNS = EXP / "artifacts/log_runs"


def select_runs():
    candidates = [("base"+str(i+1), "baseline", tag, "ycsb_band_"+tag+"_260910", "byte copy")
                  for i,tag in enumerate(ORDER)]
    candidates.append(("F2-copy", "f2load", "copy", "ycsb_band_f2_260910", "byte copy"))
    for path in sorted(RUNS.glob("ycsb_f2band_*_260911")):
        tag = path.name.split("_")[2]
        candidates.append(("F2-"+tag, "f2load", tag, path.name, "fresh load; no byte copy"))
    selected, omitted, snapshots = [], [], {}
    for label,system,tag,run,protocol in candidates:
        state_path = RUNS / run / "status.json"
        state = json.loads(state_path.read_text())
        snapshots[run] = state
        if system == "f2load" and tag in EXCLUDE:
            omitted.append(dict(run_id=run,reason=EXCLUDE[tag],status=state["kind"]))
            continue
        if state.get("kind") != "COMPLETED" or state.get("valid_full") != 6:
            omitted.append(dict(run_id=run,reason="A-F campaign incomplete at selection time",status=state.get("kind")))
            continue
        cells = read_cells(run, system)
        assert set(cells) == set(LETTERS)
        selected.append(dict(label=label,system=system,tag=tag,run_id=run,protocol=protocol,cells=cells))
    assert len([s for s in selected if s["system"] == "baseline"]) == 10
    assert any(s["system"] == "f2load" for s in selected)
    return selected, omitted, snapshots


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir",type=Path,default=EXP / "artifacts/analysis/ycsb_repeat_fidelity_260911")
    parser.add_argument("--plot-kind",choices=["bars","points"],default="bars")
    args = parser.parse_args()
    points = args.plot_kind == "points"
    selected, omitted, snapshots = select_runs()
    created = datetime.now(timezone.utc).isoformat()
    out = args.output_dir
    out.mkdir(parents=True,exist_ok=True)
    data, records, sources, refs = {}, [], set(), {}
    for sample in selected:
        label, run = sample["label"], sample["run_id"]
        data[label] = {}
        for w,row in sample["cells"].items():
            opts = command_options(row)
            refs.setdefault(w,(opts,row["binary_sha256"]))
            assert (opts,row["binary_sha256"]) == refs[w],(run,w)
            metrics = get_metrics(row,"md0",sources)
            assert metrics["positive_lookups_pct"] is None or 0 <= metrics["positive_lookups_pct"] <= 100
            data[label][w] = metrics
            records.append(dict(series=label,system=sample["system"],workload=w.upper(),**metrics,
                                source_run=run,source_protocol=sample["protocol"],source_db_dir=row["source_db_dir"],
                                measured_seconds=row["measured_seconds"],process_elapsed_sec=row["process_elapsed_sec"],
                                started_epoch=row["started_epoch"],ended_epoch=row["ended_epoch"],
                                binary_sha256=row["binary_sha256"],status=row["status"]))
            bundle = RESULTS / run
            sources.update([bundle/"results.json",bundle/"manifest.json",bundle/"provenance/environment.txt",
                            bundle/"evidence/full"/row["workload"]/row["system"]/"command.json"])
    labels = [s["label"] for s in selected]
    base_labels = [s["label"] for s in selected if s["system"] == "baseline"]
    f2_labels = [s["label"] for s in selected if s["system"] == "f2load"]
    nbase, nf2 = len(base_labels), len(f2_labels)
    tsv(out/"raw_metrics.tsv",records)
    manifest_rows = [{k:v for k,v in s.items() if k!="cells"} for s in selected]
    tsv(out/"selected_runs.tsv",manifest_rows)
    summary, per_run = [], []
    metrics = [m for _,_,group in GROUPS for m in group]
    for key,title,unit,scale in metrics:
        for w in LETTERS:
            row = dict(metric=key,title=title,workload=w.upper(),unit=unit)
            for name,group in [("baseline",base_labels),("f2load",f2_labels)]:
                vals = [data[s][w][key]/scale for s in group if data[s][w][key] is not None]
                row.update({name+"_"+stat:func(vals) if vals else None
                            for stat,func in [("mean",stats.mean),("min",min),("max",max)]})
            summary.append(row)
            per_run.append(dict(metric=key,title=title,workload=w.upper(),unit=unit,
                                **{s:data[s][w][key]/scale if data[s][w][key] is not None else None for s in labels}))
    tsv(out/"summary.tsv",summary)
    tsv(out/"per_run_display.tsv",per_run)

    base_colors = [plt.cm.Blues(0.88-0.53*i/max(1,nbase-1)) for i in range(nbase)]
    f2_colors = [plt.cm.Oranges(0.88-0.40*i/max(1,nf2-1)) for i in range(nf2)]
    colors = dict(zip(labels,base_colors+f2_colors))
    plt.rcParams.update({"font.family":"DejaVu Sans","font.size":14,"axes.titlesize":18,"axes.labelsize":15})
    handles = [Line2D([],[],linestyle="none",marker="o",markersize=7,color=colors[s],label=s)
               if points else Patch(color=colors[s],label=s) for s in labels]
    panel_audit = []

    def panel(fig, position, title, unit, scale, keys):
        ax = fig.add_subplot(position)
        step = 0.86/len(labels)
        maximum = 0
        observations = 0
        for i,label in enumerate(labels):
            xs = [x-0.43+(i+0.5)*step for x in range(6)]
            bottom = [0.0]*6
            for component,key in enumerate(keys):
                vals = [data[label][w][key] for w in LETTERS]
                heights = [v/scale if v is not None else 0 for v in vals]
                if points:
                    present = [(x,y) for x,y,v in zip(xs,heights,vals) if v is not None]
                    observations += len(present)
                    if present:
                        px,py = zip(*present)
                        ax.scatter(px,py,s=34 if component else 26,
                                   marker="^" if component else "o",
                                   facecolors="none" if component else [colors[label]],
                                   edgecolors=[colors[label]] if component else "#324d63",
                                   linewidths=0.9 if component else 0.3,zorder=3,clip_on=False)
                        maximum = max(maximum,max(py))
                else:
                    ax.bar(xs,heights,bottom=bottom,width=step*0.90,color=colors[label],
                           hatch="////" if component else None,edgecolor="#43576a" if component else "none",
                           linewidth=0.18 if component else 0)
                    bottom = [a+b for a,b in zip(bottom,heights)]
            if not points:
                maximum = max(maximum,max(bottom))
        for i,w in enumerate(LETTERS):
            if all(data[s][w][keys[0]] is None for s in labels):
                ax.text(i,0.035,"N/A",fontsize=13,ha="center",transform=ax.get_xaxis_transform())
        ax.set_xticks(range(6),list(LETTERS.upper()))
        ax.set_title(title,loc="left",pad=12)
        ax.set_ylabel(unit)
        ax.set_xlim(-0.7,5.7)
        ax.set_ylim(0,maximum*(1.30 if len(keys)>1 else 1.13) if maximum else 1)
        style(ax)
        if keys == ["positive_lookups_pct"]:
            ax.set_ylim(0,100)
            ax.set_yticks(range(0,101,20))
            ax.yaxis.set_major_formatter(PercentFormatter(xmax=100,decimals=0))
        if len(keys)>1:
            components = ([Line2D([],[],linestyle="none",marker="o",color="#52697a",markersize=7,label="Read"),
                           Line2D([],[],linestyle="none",marker="^",markerfacecolor="none",color="#52697a",markersize=8,label="Write")]
                          if points else
                          [Patch(facecolor="#d9e2ec",edgecolor="#43576a",label="Read"),
                           Patch(facecolor="#d9e2ec",edgecolor="#43576a",hatch="////",label="Write")])
            ax.legend(handles=components,
                      loc="upper right",frameon=False,ncol=2,fontsize=14)
        assert ax.get_ylim()[0] == 0
        assert ax.get_yscale() == "linear"
        panel_audit.append(dict(title=title,metrics=keys,unit=unit,ymin=ax.get_ylim()[0],ymax=ax.get_ylim()[1],
                                plot_kind=args.plot_kind,observations=observations if points else None))

    def header(fig,title):
        fig.suptitle(title,fontsize=23,y=0.987)
        fig.legend(handles=handles,ncol=max(7,(len(labels)+1)//2),loc="upper center",bbox_to_anchor=(0.5,0.945),frameon=False,
                   fontsize=14,columnspacing=1.35,handlelength=1.35)

    fig = plt.figure(figsize=(17,10))
    grid = fig.add_gridspec(2,6,left=0.075,right=0.985,bottom=0.14,top=0.79,hspace=0.55,wspace=0.96)
    for spec in [
        (grid[0,:2],"Throughput","K ops/s",1000,["throughput_ops_sec"]),
        (grid[0,2:4],"Mean latency","us",1,["avg_latency_us"]),
        (grid[0,4:],"Filter checks per lookup","checks / lookup",1,["filter_checks_per_get"]),
        (grid[1,:3],"Positive lookups","Successful / total lookups",1,["positive_lookups_pct"]),
        (grid[1,3:],"Compaction bytes","GiB",GIB,["compaction_read_bytes","compaction_write_bytes"]),
    ]:
        panel(fig,*spec)
    header(fig,"YCSB fidelity | %d baseline loads, %d F2Load states" % (nbase,nf2))
    mark_note = ("Each point is one run; horizontal offsets separate repeats. Compaction: circle = read, triangle = write."
                 if points else "1000 GiB input | 50 GiB cache | 48 threads | 300 s per workload. Compaction bars stack read + write.")
    fig.text(0.075,0.080,mark_note,fontsize=12,color="#52697a")
    fig.text(0.075,0.050,"Positive lookups = found Get / all Get requests; E: N/A. Every workload starts on a fresh clone.",fontsize=12,color="#52697a")
    fig.text(0.075,0.020,"Baseline and F2-copy: byte copies. Other F2 states: fresh loads without byte recopy. f01 excluded by existing analysis policy.",fontsize=12,color="#52697a")
    fig.savefig(out/"fidelity.png",dpi=180)
    fig.savefig(out/"fidelity.pdf")

    io = plt.figure(figsize=(16,11))
    grid = io.add_gridspec(2,2,left=0.085,right=0.98,bottom=0.10,top=0.79,hspace=0.44,wspace=0.34)
    for spec in [
        (grid[0,0],"I/O read counts","million I/Os",1e6,["device_read_ios"]),
        (grid[0,1],"I/O write counts","million I/Os",1e6,["device_write_ios"]),
        (grid[1,0],"I/O read bytes","GiB",GIB,["device_read_bytes"]),
        (grid[1,1],"I/O write bytes","GiB",GIB,["device_write_bytes"]),
    ]:
        panel(io,*spec)
    header(io,"YCSB device I/O | %d baseline loads, %d F2Load states" % (nbase,nf2))
    io.text(0.085,0.049,"1000 GiB input | 50 GiB cache | 48 threads | 300 s workload. All y-axes start at zero; individual runs are shown.",fontsize=12,color="#52697a")
    io.text(0.085,0.021,"md0 totals cover the whole process window, including DB open/close and other device activity.",fontsize=12,color="#52697a")
    io.savefig(out/"io.png",dpi=180)
    io.savefig(out/"io.pdf")
    with PdfPages(out/"all_figures.pdf") as pdf:
        pdf.savefig(fig)
        pdf.savefig(io)
    plt.close(fig)
    plt.close(io)

    note = ("Selected complete campaigns only, using plot_baseline_band.py's existing f01 exclusion. "
            "Baseline DBs and F2-copy were byte-copied once per source DB; other F2 states were newly loaded without byte recopy. "
            "Every workload uses a fresh hardlink SST clone with private metadata and a page-cache reset. "
            "YCSB options and executable are identical across included cells. Observations are per loaded DB, not repeated reads of one shared mutable DB. "
            "This is state-performance comparison, not proof of identical key membership. Loading metadata in source bundles is not used here.")
    manifest = dict(created_utc=created,selected=manifest_rows,omitted=omitted,status_snapshots=snapshots,
                    validated_cells=len(records),settings=dict(dataset_gib=1000,key_bytes=24,value_bytes=1000,
                    cache_size_bytes=50*GIB,threads=48,duration_sec=300,binary_sha256=records[0]["binary_sha256"]),
                    metric_definitions=DEFINITIONS,protocol=note,panel_audit=panel_audit,plot_kind=args.plot_kind,
                    reproduce_argv=["python3","experiments/analysis/plot_ycsb_repeat_fidelity.py",
                                    "--plot-kind",args.plot_kind,"--output-dir",str(out)])
    for name in ["plot_ycsb_repeat_fidelity.py","plot_ycsb_raw_metrics.py","compare_five_baseline_ycsb.py","plot_baseline_band.py"]:
        sources.add(Path(__file__).resolve().with_name(name))
    manifest["source_sha256"] = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)}
    (out/"provenance.json").write_text(json.dumps(manifest,indent=2)+"\n")
    lines = ["# Repeated YCSB fidelity comparison", "", "Generated: "+created, "", note, "",
             "Included: %d baseline states and %d F2Load states, %d full cells." % (nbase,nf2,len(records)), "",
             "| Label | Run ID | Source preparation |", "|---|---|---|"]
    lines.extend("| %s | %s | %s |" % (s["label"],s["run_id"],s["protocol"]) for s in selected)
    lines.extend(["", "Excluded or incomplete:", ""]+["- %s: %s (%s)" % (s["run_id"],s["reason"],s["status"]) for s in omitted])
    compaction_note = ("Each point is one run. Compaction Read and Write are separate, unstacked values (circle and triangle). "
                       "Horizontal offsets only separate repeats; y-values have no jitter. "
                       if points else "Compaction totals stack Read and Write, with an explicit legend. ")
    lines.extend(["", "All axes are linear and start at zero; positive lookups is successful Get / total Get requests with a fixed 0-100% axis. "
                  + compaction_note + "I/O is a separate figure. "
                  "raw_metrics.tsv retains counts and bytes; summary.tsv and per_run_display.tsv retain display units. "
                  "No percentiles or baseline-relative normalization are plotted."])
    (out/"README.md").write_text("\n".join(lines)+"\n")
    page = ["<!doctype html><html lang='en'><meta charset='utf-8'><title>Repeated YCSB fidelity</title>",
            "<style>body{font:18px system-ui;max-width:1700px;margin:32px auto;padding:0 20px;color:#243b53}p{line-height:1.6}img{width:100%}table{border-collapse:collapse;width:100%;font-size:16px}th,td{padding:8px;border-bottom:1px solid #d9e2ec;text-align:right}td:first-child,th:first-child{text-align:left}details{margin:20px 0}.scroll{overflow:auto}</style>",
            "<h1>YCSB fidelity: %d baseline loads and %d F2Load states</h1>" % (nbase,nf2),
            "<p>"+html.escape(note)+"</p><p>Selected at "+created+". "+html.escape("; ".join(s["run_id"]+": "+s["reason"] for s in omitted))+"</p>"]
    for name,title in [("fidelity.png","Fidelity metrics"),("io.png","Device I/O")]:
        encoded = base64.b64encode((out/name).read_bytes()).decode()
        page.append("<h2>"+title+"</h2><img alt='"+title+"' src='data:image/png;base64,"+encoded+"'>")
    page.append("<h2>Numerical summaries (mean and observed range)</h2><table><tr><th>Metric</th><th>Workload</th><th>Unit</th><th>Baseline mean</th><th>Baseline min-max</th><th>F2Load mean</th><th>F2Load min-max</th></tr>")
    for row in summary:
        values = [row["title"],row["workload"],row["unit"],format_value(row["baseline_mean"]),
                  format_value(row["baseline_min"])+" – "+format_value(row["baseline_max"]),format_value(row["f2load_mean"]),
                  format_value(row["f2load_min"])+" – "+format_value(row["f2load_max"])]
        page.append("<tr>"+"".join("<td>"+html.escape(v)+"</td>" for v in values)+"</tr>")
    page.append("</table><details><summary>Every individual measurement</summary><div class='scroll'><table><tr><th>Metric</th><th>Workload</th><th>Unit</th>"+"".join("<th>"+s+"</th>" for s in labels)+"</tr>")
    for row in per_run:
        values = [row["title"],row["workload"],row["unit"]]+[format_value(row[s]) for s in labels]
        page.append("<tr>"+"".join("<td>"+html.escape(v)+"</td>" for v in values)+"</tr>")
    page.append("</table></div></details></html>")
    (out/"report.html").write_text("\n".join(page))
    print("Output:",out)
    print("Included:",labels)
    print("Omitted:",omitted)
    print("Validated %d cells; 5 main panels, 4 separate I/O panels." % len(records))


if __name__ == "__main__":
    main()
