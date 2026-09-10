#!/usr/bin/env python3
"""Plot measured YCSB units for five baseline DBs and latest F2Load.

Never divide a series by baseline performance. Totals cover each recorded run;
filter checks per Get is an explicitly labeled physical work-per-lookup metric.
Device counters include the whole process window and other device activity.
"""
import argparse
import base64
import hashlib
import html
import json
from pathlib import Path
import statistics

from compare_five_baseline_ycsb import (
    BASELINES, EXP, LATEST, LETTERS, RESULTS, RERUNS,
    command_options, read_cells, tsv,
)

SERIES = BASELINES + ["F2Load"]
DISPLAY_LABELS = dict(zip(SERIES, ["base1", "base2", "base3", "base4", "base5", "F2Load"]))
DISPLAY_SERIES = [DISPLAY_LABELS[s] for s in SERIES]
COLORS = ["#334e68", "#627d98", "#829ab1", "#9fb3c8", "#bcccdc", "#d36b29"]
GIB = 1024 ** 3
# key, title, display unit, raw units per display unit
GROUPS = [
    ("01_performance", "Throughput and latency", [
        ("throughput_ops_sec", "Throughput", "K ops/s", 1000),
        ("avg_latency_us", "Mean operation latency", "us", 1),
    ]),
    ("02_filter", "Filter access and lookup", [
        ("filter_checks_per_get", "Filter checks per lookup", "checks / lookup", 1),
    ]),
    ("03_positive", "Positive lookups", [
        ("positive_lookups_pct", "Positive lookups", "% of Get requests", 1),
    ]),
    ("04_compaction", "Compaction bytes", [
        ("compaction_read_bytes", "Compaction reads", "GiB", GIB),
        ("compaction_write_bytes", "Compaction writes", "GiB", GIB),
    ]),
    ("05_io", "Device I/O counts and bytes (md0)", [
        ("device_read_ios", "Completed read I/Os", "million I/Os", 1e6),
        ("device_write_ios", "Completed write I/Os", "million I/Os", 1e6),
        ("device_read_bytes", "Device bytes read", "GiB", GIB),
        ("device_write_bytes", "Device bytes written", "GiB", GIB),
    ]),
]
DEFINITIONS = {
    "filter_checks_per_get": "(rocksdb.bloom.filter.useful + rocksdb.bloom.filter.full.positive) / rocksdb.number.keys.read. Includes the Get in RMW. Undefined for E, which performs scans/inserts.",
    "positive_lookups": "Key-found Get calls: memtable.hit + l0.hit + l1.hit + l2andup.hit; includes the Get in RMW.",
    "positive_lookups_pct": "100 * positive_lookups / total_lookup_requests, where total_lookup_requests = rocksdb.number.keys.read. The denominator is all Get requests, including the Get in RMW. E has no Get requests and is N/A.",
    "compaction_read_bytes": "rocksdb.compact.read.bytes; separate from logical rocksdb.bytes.read and device read bytes.",
    "compaction_write_bytes": "rocksdb.compact.write.bytes; excludes flush bytes.",
    "device_io": "md0 /proc/diskstats end minus start: read completed field 1, write completed field 5; sectors read field 3 and sectors written field 7 times 512 bytes. Fields are numbered after device name. These are device requests, not application Get calls or physical per-disk operations.",
    "latency": "Aggregate db_bench mean operation latency in microseconds.",
    "windows": "Engine tickers cover statistics-object lifetime, including open/background work. Device deltas cover the whole recorded process window including open/close and any concurrent host I/O. Totals are not divided by operation count or forced to exactly 300 s.",
}


def diskstats(path, device):
    lines = [line.split() for line in path.read_text().splitlines()]
    fields = next(parts for parts in lines if parts[2] == device)
    return dict(device_read_ios=int(fields[3]), device_write_ios=int(fields[7]),
                device_read_bytes=int(fields[5]) * 512,
                device_write_bytes=int(fields[9]) * 512)


def get_metrics(row, device, sources):
    ticker = row["tickers"]
    full = ticker["rocksdb.bloom.filter.full.positive"]
    useful = ticker["rocksdb.bloom.filter.useful"]
    assert ticker["rocksdb.bloom.filter.prefix.checked"] == 0
    gets = row["engine_keys_read"]
    data = {key: row[key] for key in ["throughput_ops_sec", "avg_latency_us",
                                    "compaction_read_bytes", "compaction_write_bytes"]}
    assert 0 <= row["successful_gets"] <= gets
    data.update(positive_lookups=row["successful_gets"], total_lookup_requests=gets,
                positive_lookups_pct=100*row["successful_gets"]/gets if gets else None,
                filter_checks_per_get=(useful+full)/gets if gets else None)
    raw = Path(row["log_dir"]) / "raw"
    start, end = (raw / ("diskstats." + suffix) for suffix in ("start", "end"))
    a, b = diskstats(start, device), diskstats(end, device)
    deltas = {key: b[key]-a[key] for key in a}
    assert all(value >= 0 for value in deltas.values()), raw
    data.update(deltas)
    sources.update([start, end])
    return data


def style(ax):
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color("#bdc4ca")
    ax.grid(axis="y", linewidth=0.5, alpha=0.25)
    ax.set_axisbelow(True)
    ax.tick_params(labelsize=12, length=3, colors="#334e68")
    ax.ticklabel_format(axis="y", style="plain", useOffset=False)
    ax.set_ylim(bottom=0)


def format_value(value):
    if value is None:
        return "N/A"
    if value == 0:
        return "0"
    if abs(value) < 0.001:
        return f"{value:.8f}".rstrip("0")
    return f"{value:,.3f}" if abs(value) < 1 else f"{value:,.2f}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=EXP / "artifacts/analysis/ycsb_selected_metrics_260909")
    parser.add_argument("--device", default="md0")
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    cells = {"run3": read_cells(LATEST, "baseline")}
    for label, suffix in RERUNS.items():
        cells[label] = read_cells("baseline_repeat_ycsb_all_260908_night_"+label)
        cells[label].update(read_cells("baseline_repeat_ycsb_all_260909_"+label+"_"+suffix))
    cells["F2Load"] = read_cells(LATEST, "f2load")
    data, records, sources = {}, [], set()
    for label in SERIES:
        data[label] = {}
        for w in LETTERS:
            row = cells[label][w]
            assert command_options(row) == command_options(cells["F2Load"][w]), (label, w)
            assert row["binary_sha256"] == cells["F2Load"][w]["binary_sha256"]
            data[label][w] = get_metrics(row, args.device, sources)
            records.append(dict(series=DISPLAY_LABELS[label], source_series=label, workload=w.upper(), **data[label][w],
                                source_run=row["source_run"], source_db_dir=row["source_db_dir"],
                                measured_seconds=row["measured_seconds"], process_elapsed_sec=row["process_elapsed_sec"],
                                started_epoch=row["started_epoch"], ended_epoch=row["ended_epoch"],
                                binary_sha256=row["binary_sha256"]))
            bundle = RESULTS / row["source_run"]
            sources.update([bundle/"results.json", bundle/"manifest.json", bundle/"provenance/environment.txt",
                            bundle/"evidence/full"/row["workload"]/row["system"]/"command.json"])
    tsv(out / "raw_metrics.tsv", records)
    all_metrics = [metric for _,_,group in GROUPS for metric in group]
    summary = []
    for key,title,unit,scale in all_metrics:
        for w in LETTERS:
            values = [data[b][w][key] for b in BASELINES]
            valid = [v for v in values if v is not None]
            summary.append(dict(metric=key, title=title, workload=w.upper(), display_unit=unit,
                                **{DISPLAY_LABELS[b]:data[b][w][key]/scale if data[b][w][key] is not None else None for b in SERIES},
                                baseline_mean=statistics.mean(valid)/scale if valid else None,
                                baseline_min=min(valid)/scale if valid else None,
                                baseline_max=max(valid)/scale if valid else None))
    tsv(out / "display_metrics.tsv", summary)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch
    from matplotlib.ticker import PercentFormatter
    from matplotlib.backends.backend_pdf import PdfPages
    plt.rcParams.update({"font.family":"DejaVu Sans", "font.size":14,
                         "axes.titlesize":17, "axes.labelsize":15})
    series_handles = [Patch(color=c,label=s) for c,s in zip(COLORS,DISPLAY_SERIES)]

    def draw_panel(fig, position, title, unit, scale, keys):
        ax = fig.add_subplot(position)
        maximum = 0
        for i,label in enumerate(SERIES):
            xs = [x-0.35+i*0.14 for x in range(6)]
            bottom = [0.0]*6
            for component,key in enumerate(keys):
                values = [data[label][w][key] for w in LETTERS]
                heights = [v/scale if v is not None else 0 for v in values]
                ax.bar(xs,heights,bottom=bottom,width=0.122,color=COLORS[i],
                       hatch="////" if component else None,
                       edgecolor="#43576a" if component else "none",
                       linewidth=0.2 if component else 0)
                bottom = [a+b for a,b in zip(bottom,heights)]
                if i == len(SERIES)-1:
                    for w,v in enumerate(values):
                        if v is None:
                            ax.text(w,0.035,"N/A",fontsize=12,ha="center",transform=ax.get_xaxis_transform())
            maximum = max(maximum,max(bottom))
        ax.set_xticks(range(6),list(LETTERS.upper()))
        ax.set_title(title,loc="left",pad=12)
        ax.set_ylabel(unit)
        ax.set_ylim(0,maximum*(1.30 if len(keys)>1 else 1.13) if maximum else 1)
        style(ax)
        if keys == ["positive_lookups_pct"]:
            ax.set_ylim(0,100)
            ax.set_yticks(range(0,101,20))
            ax.yaxis.set_major_formatter(PercentFormatter(xmax=100,decimals=0))
        if len(keys) > 1:
            ax.legend(handles=[Patch(facecolor="#d9e2ec",edgecolor="#43576a",label="Read"),
                               Patch(facecolor="#d9e2ec",edgecolor="#43576a",hatch="////",label="Write")],
                      loc="upper right",frameon=False,ncol=2,fontsize=14)
        assert ax.get_ylim()[0] == 0
        return ax

    fig = plt.figure(figsize=(15,8.8))
    grid = fig.add_gridspec(2,6,left=0.075,right=0.985,bottom=0.12,
                           top=0.82,hspace=0.53,wspace=0.95)
    main_panels = [
        (grid[0,:2],"Throughput","K ops/s",1000,["throughput_ops_sec"]),
        (grid[0,2:4],"Mean latency","us",1,["avg_latency_us"]),
        (grid[0,4:],"Filter checks per lookup","checks / lookup",1,["filter_checks_per_get"]),
        (grid[1,:3],"Positive lookups","Successful / total lookups",1,["positive_lookups_pct"]),
        (grid[1,3:],"Compaction bytes","GiB",GIB,["compaction_read_bytes","compaction_write_bytes"]),
    ]
    for panel in main_panels:
        draw_panel(fig,*panel)
    fig.legend(handles=series_handles,ncol=6,loc="upper center",bbox_to_anchor=(0.5,0.935),frameon=False,fontsize=15)
    fig.suptitle("YCSB A-F | baseline and F2Load",fontsize=23,y=0.985)
    fig.text(0.075,0.062,"50 GiB cache | 48 threads | 300 s workload. Compaction bars stack read + write.",fontsize=12,color="#52697a")
    fig.text(0.075,0.030,"Positive lookups = found Get / all Get requests. E: N/A (no Get requests).",fontsize=12,color="#52697a")
    fig.savefig(out/"overview.png",dpi=180)
    fig.savefig(out/"main_metrics.pdf")

    io_fig = plt.figure(figsize=(14,10))
    io_grid = io_fig.add_gridspec(2,2,left=0.085,right=0.98,bottom=0.10,
                                 top=0.83,hspace=0.43,wspace=0.33)
    io_panels = [
        (io_grid[0,0],"I/O read counts","million I/Os",1e6,["device_read_ios"]),
        (io_grid[0,1],"I/O write counts","million I/Os",1e6,["device_write_ios"]),
        (io_grid[1,0],"I/O read bytes","GiB",GIB,["device_read_bytes"]),
        (io_grid[1,1],"I/O write bytes","GiB",GIB,["device_write_bytes"]),
    ]
    for panel in io_panels:
        draw_panel(io_fig,*panel)
    io_fig.legend(handles=series_handles,ncol=6,loc="upper center",bbox_to_anchor=(0.5,0.935),frameon=False,fontsize=15)
    io_fig.suptitle("YCSB A-F | device I/O",fontsize=23,y=0.985)
    io_fig.text(0.085,0.049,"50 GiB cache | 48 threads | 300 s workload. All y-axes start at zero.",fontsize=12,color="#52697a")
    io_fig.text(0.085,0.021,"md0 totals cover the whole process window, including DB open/close and other device activity.",fontsize=12,color="#52697a")
    io_fig.savefig(out/"io.png",dpi=180)
    io_fig.savefig(out/"io_metrics.pdf")
    with PdfPages(out/"all_metrics.pdf") as pdf:
        pdf.savefig(fig)
        pdf.savefig(io_fig)
    plt.close(fig)
    plt.close(io_fig)

    caveat = "Label mapping: base1=run3, base2=n01, base3=n02, base4=n03, base5=n04. Baseline selection is unchanged: run3 from the latest paired campaign; n01 all rerun; n02/n03 A rerun; n04 A/F rerun. Other overnight cells retained. Original disk occupancy 92-95%, reruns 58%, latest campaign 60%. F2Load has one run per workload. Engine and device counter windows include open/background work. Higher throughput performs more operations in 300 s, so raw total counts alone do not establish greater per-operation cost."
    report = ["# YCSB measured values: five baselines and latest F2Load", "", caveat, "",
              "All y-axes begin at zero. No metric is divided by baseline values. Total counts/bytes are provided in exact raw units in raw_metrics.tsv; display_metrics.tsv uses K ops/s, millions and GiB. Filter checks per lookup and positive lookup success rate use Get requests as the denominator; both are undefined for E's scan/insert workload. Positive lookups has a fixed 0-100% axis.", "",
              "overview.png and main_metrics.pdf contain throughput, mean latency, filter checks per lookup, positive lookup success rate, and compaction bytes. io.png and io_metrics.pdf contain the separate I/O figure. all_metrics.pdf contains both figures on separate pages. Compaction has an explicit Read/Write legend and stacks the two components.", "", "Metric definitions:", ""]
    report.extend("- "+key+": "+value for key,value in DEFINITIONS.items())
    (out/"README.md").write_text("\n".join(report)+"\n")
    content = ["<!doctype html><html lang='en'><meta charset='utf-8'><title>YCSB measured metrics</title>",
               "<style>body{font:18px system-ui;margin:32px auto;max-width:1600px;color:#243b53;padding:0 18px}h1,h2{font-weight:600}p{line-height:1.6}img{width:100%;height:auto}table{border-collapse:collapse;width:100%;font-size:16px}th,td{padding:9px 12px;border-bottom:1px solid #d9e2ec;text-align:right}th:first-child,td:first-child{text-align:left}thead{background:#f0f4f8}details{margin:20px 0}nav a{margin-right:20px} .note{background:#f0f4f8;padding:16px} .scroll{overflow:auto}</style>",
               "<h1>YCSB: five baseline DBs and latest F2Load</h1><p>Absolute measured units; all y-axes start at zero. Input 1000 GiB, KV 24 + 1000 bytes, 50 GiB cache, 48 threads, 300 s per workload. Totals cover each recorded run.</p>",
               "<p class='note'>"+html.escape(caveat)+"</p><nav>"]
    content.extend(f"<a href='#{key}'>{html.escape(title)}</a>" for key,title,_ in GROUPS)
    content.append("</nav>")
    for image_name, image_title in [("overview.png","YCSB metrics"),("io.png","I/O counts and bytes")]:
        encoded=base64.b64encode((out/image_name).read_bytes()).decode()
        content.append("<h2>"+image_title+"</h2><img alt='"+image_title+"' src='data:image/png;base64,"+encoded+"'>")
    for filename,title,metrics in GROUPS:
        content.extend([f"<h2 id='{filename}'>{html.escape(title)}</h2>",
                        "<div class='scroll'><table><thead><tr><th>Metric (unit)</th><th>Workload</th>"+"".join("<th>"+s+"</th>" for s in DISPLAY_SERIES)+"<th>Baseline mean</th></tr></thead><tbody>"])
        keys={m[0] for m in metrics}
        for row in summary:
            if row["metric"] not in keys:continue
            content.append("<tr><td>"+html.escape(row["title"]+" ("+row["display_unit"]+")")+"</td><td>"+row["workload"]+"</td>"+"".join("<td>"+format_value(row[s])+"</td>" for s in DISPLAY_SERIES+["baseline_mean"])+"</tr>")
        content.append("</tbody></table></div>")
    content.append("<h2>Metric definitions and scope</h2><ul>")
    content.extend("<li><b>"+html.escape(k)+"</b>: "+html.escape(v)+"</li>" for k,v in DEFINITIONS.items())
    content.append("</ul></html>")
    (out/"report.html").write_text("\n".join(content))
    sources.update([Path(__file__).resolve(), Path(__file__).resolve().with_name("compare_five_baseline_ycsb.py")])
    sources.update([EXP/"analysis/parse_ycsb_alternatives.py", EXP/"scripts/read/run_ycsb_alternatives.py"])
    (out/"provenance.json").write_text(json.dumps({
        "command":"python3 experiments/analysis/plot_ycsb_raw_metrics.py --device "+args.device,
        "source_sha256":{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(sources)},
        "definitions":DEFINITIONS,"selection_and_limits":caveat,"display_labels":DISPLAY_LABELS,
        "validated_cells":len(records),"device":args.device,
        "positive_lookup_denominator":"all Get requests (rocksdb.number.keys.read)",
        "checks":"36 full cells: status ok, no timeout or missing tickers; same binary and actual options except DB/report paths; nonnegative device deltas; no prefix checks; all plots use linear axes starting at zero."
    },indent=2)+"\n")
    print("Wrote",out,"with",len(records),"cells and",len(all_metrics),"metrics")
    for row in summary:
        print(row["metric"],row["workload"],"baseline_mean",format_value(row["baseline_mean"]),"F2Load",format_value(row["F2Load"]),row["display_unit"])


if __name__ == "__main__":
    main()
