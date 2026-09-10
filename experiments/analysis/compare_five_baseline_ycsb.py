#!/usr/bin/env python3
"""Compare five baseline DB states with the September 9 F2Load campaigns.

Read archived measurements only. Keep same-DB remeasurements out of the sample
count and retain the original overnight cells for sensitivity analysis.
"""
import argparse
import csv
import hashlib
import json
from pathlib import Path
import statistics as stats

EXP = Path(__file__).resolve().parents[1]
RESULTS = EXP / "results"
LATEST = "ycsb_50g_f2ratio_260909"
BEFORE = "ycsb_50g_f2load_baseline_260909"
LETTERS = "abcdef"
BASELINES = ["run3", "n01", "n02", "n03", "n04"]
RERUNS = {"n01": "all", "n02": "a", "n03": "a", "n04": "af"}


def read_cells(run, system=None):
    rows = json.loads((RESULTS / run / "results.json").read_text())
    selected = {}
    for row in rows:
        if row["phase"] != "full" or (system and row["system"] != system):
            continue
        letter = row["workload"][-1]
        assert letter not in selected, (run, system, letter)
        assert row["status"] == "ok" and row["exit_code"] == 0
        assert not row["timed_out"] and not row["missing_tickers"]
        assert not row["warnings"], row["warnings"]
        selected[letter] = dict(row, source_run=run)
    return selected


def command_options(row):
    path = RESULTS / row["source_run"] / "evidence/full" / row["workload"] / row["system"] / "command.json"
    command = json.loads(path.read_text())
    return {arg.split("=", 1)[0]: arg.split("=", 1)[1]
            for arg in command[1:] if arg.startswith("--") and "=" in arg
            and arg.split("=", 1)[0] not in {"--db", "--report_file"}}


def tsv(path, rows):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=EXP / "artifacts/analysis/ycsb_five_baseline_comparison_260909")
    args = parser.parse_args()
    out = args.output_dir
    out.mkdir(parents=True, exist_ok=True)
    baseline = {"run3": read_cells(LATEST, "baseline")}
    original = {}
    for label, suffix in RERUNS.items():
        original[label] = read_cells("baseline_repeat_ycsb_all_260908_night_" + label)
        baseline[label] = dict(original[label])
        baseline[label].update(read_cells("baseline_repeat_ycsb_all_260909_" + label + "_" + suffix))
    f2, old = read_cells(LATEST, "f2load"), read_cells(BEFORE, "f2load")
    series = dict(baseline, f2load_latest=f2, f2load_before=old)
    selected, summary, tails, sensitivity = [], [], [], []
    for label, cells in series.items():
        assert set(cells) == set(LETTERS)
        for w, row in cells.items():
            assert command_options(row) == command_options(f2[w]), (label, w)
            assert row["binary_sha256"] == f2[w]["binary_sha256"]
            selected.append(dict(series=label, workload=w.upper(), source_run=row["source_run"],
                                 **{k: row[k] for k in ["system", "source_db_dir", "throughput_ops_sec", "avg_latency_us",
                                                       "get_found_fraction", "operations", "measured_seconds", "status",
                                                       "binary_sha256", "started_epoch", "ended_epoch"]}))
    for w in LETTERS:
        values = [baseline[b][w]["throughput_ops_sec"] for b in BASELINES]
        mean, sd = stats.mean(values), stats.stdev(values)
        fv, ov = f2[w]["throughput_ops_sec"], old[w]["throughput_ops_sec"]
        summary.append(dict(workload=w.upper(), **dict(zip(BASELINES, values)), baseline_mean=mean,
                            baseline_sample_sd=sd, baseline_cv_pct=100 * sd / mean,
                            baseline_min=min(values), baseline_max=max(values), f2load_latest=fv,
                            f2_vs_mean_pct=100 * (fv / mean - 1),
                            f2_vs_paired_run3_pct=100 * (fv / values[0] - 1),
                            f2load_before=ov, before_vs_mean_pct=100 * (ov / mean - 1),
                            latest_vs_before_pct=100 * (fv / ov - 1),
                            baseline_avg_latency_us=stats.mean(baseline[b][w]["avg_latency_us"] for b in BASELINES),
                            f2_avg_latency_us=f2[w]["avg_latency_us"]))
        for operation, hist in f2[w]["operation_histograms"].items():
            bv = baseline["run3"][w]["operation_histograms"][operation]["p99_us"]
            tails.append(dict(workload=w.upper(), operation=operation, paired_baseline_p99_us=bv,
                              f2_p99_us=hist["p99_us"], change_pct=100 * (hist["p99_us"] / bv - 1)))
        raw_values = [values[0]] + [original[b][w]["throughput_ops_sec"] for b in BASELINES[1:]]
        sensitivity.append(dict(workload=w.upper(), original_overnight_plus_run3_mean=stats.mean(raw_values),
                                selected_mean=mean, f2_vs_original_mean_pct=100 * (fv / stats.mean(raw_values) - 1),
                                f2_vs_selected_mean_pct=100 * (fv / mean - 1)))
    tsv(out / "comparison.tsv", summary)
    tsv(out / "selected_cells.tsv", selected)
    tsv(out / "paired_p99.tsv", tails)
    tsv(out / "rerun_sensitivity.tsv", sensitivity)
    source_files = set()
    for run in {r["source_run"] for r in selected} | {r["source_run"] for rows in original.values() for r in rows.values()}:
        source_files.update((RESULTS / run).glob("manifest.json"))
        source_files.update((RESULTS / run).glob("results.json"))
        source_files.update((RESULTS / run / "provenance").glob("environment.txt"))
        source_files.update((RESULTS / run / "provenance").glob("source_identities.json"))
        source_files.update((RESULTS / run / "evidence/full").glob("*/*/command.json"))
    source_files.add(RESULTS / "f2load_1tb_260909_ratio_bundle/loads.json")
    source_files.add(Path(__file__).resolve())
    (out / "provenance.json").write_text(json.dumps({
        "reproduce": "python3 experiments/analysis/compare_five_baseline_ycsb.py",
        "source_sha256": {str(p.relative_to(EXP)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(source_files)},
        "checks": "All 42 selected full cells: status ok, exit 0, no timeout/warnings/missing tickers; identical YCSB binary and actual command options except DB/report paths per workload.",
        "settings": json.loads((RESULTS / LATEST / "manifest.json").read_text()),
        "baseline_selection": "run3 latest paired baseline; n01 all rerun; n02/n03 A rerun; n04 A/F rerun. Other overnight cells retained. Five distinct source DBs; reruns do not add replicates."
    }, indent=2) + "\n")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 4.8))
    xs = list(range(6))
    means = [r["baseline_mean"] for r in summary]
    ax.errorbar(xs, [1] * 6,
                yerr=[[1-r["baseline_min"]/r["baseline_mean"] for r in summary],
                      [r["baseline_max"]/r["baseline_mean"]-1 for r in summary]],
                fmt="none", ecolor="#8a969f", capsize=10, linewidth=3, label="Baseline min-max (5 DBs)")
    for i, b in enumerate(BASELINES):
        ax.scatter([x + (i-2)*0.025 for x in xs], [baseline[b][w]["throughput_ops_sec"]/m for w,m in zip(LETTERS, means)],
                   s=24, c="#788b99", alpha=0.8, zorder=3)
    ax.scatter([x-0.14 for x in xs], [r["f2load_before"]/r["baseline_mean"] for r in summary],
               s=65, marker="D", facecolors="none", edgecolors="#c86b2b", label="F2Load before", zorder=4)
    ys = [r["f2load_latest"]/r["baseline_mean"] for r in summary]
    ax.scatter([x+0.14 for x in xs], ys, s=78, marker="D", c="#c86b2b", label="F2Load latest", zorder=5)
    for x,y,r in zip(xs,ys,summary):
        ax.annotate(f'{r["f2_vs_mean_pct"]:+.1f}%', (x+0.14,y), xytext=(5,-17 if y<1 else 10), textcoords="offset points", fontsize=9, color="#a4521a")
    ax.axhline(1, color="#59656e", linestyle="--", linewidth=0.8)
    ax.set(xticks=xs, xticklabels=list(LETTERS.upper()), ylabel="Throughput / five-baseline mean", xlabel="YCSB workload",
           ylim=(0.89, 1.29), title="F2Load vs five baseline DB states | 50 GiB cache, 48 threads, 300 s")
    ax.grid(axis="y", alpha=0.18)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, ncol=3, loc="upper left", fontsize=9)
    fig.text(0.08, 0.02, "Latest same-DB reruns selected where available; remaining overnight cells retained. Ranges are observed spread, not confidence intervals.", fontsize=8, color="#59656e")
    fig.tight_layout(rect=(0,0.05,1,1))
    fig.savefig(out / "comparison.png", dpi=180)
    fig.savefig(out / "comparison.pdf")
    plt.close(fig)

    lines = ["# Five baseline DBs vs F2Load: YCSB comparison", "",
             "Inputs: archived results under experiments/results. No benchmark was run. Throughput units: ops/s; latencies: microseconds.", "",
             "Settings: 1000 GiB logical input, 24-byte keys + 1000-byte values, 48 threads, 300 seconds per full cell, 50 GiB LRU block cache, cached index/filter blocks, direct I/O, no compression, WAL disabled, automatic compaction enabled. Same frozen YCSB executable (SHA256 20d67c38612cee9a34d9ef7114c81bc8366b2d4cf99c598b642e63f21ab74266; profiler commit dbb0a44a65344f263356c507ec09762c8ed81a71). Each cell starts on a fresh clone; these are initial online-use observations.", "",
             "The five baselines are distinct source DBs: run3 and overnight n01-n04. run3 is taken from the latest F2Load paired campaign. n01 uses its full rerun; n02/n03 use rerun A; n04 uses rerun A/F. The remaining overnight cells are retained. This follows the existing plot_ycsb_across_loads.py selection. Disk conditions were not identical throughout: provenance/environment.txt records 92%, 93%, 94%, 95% occupancy for original n01-n04, 58% for reruns and F2-before, and 60% for latest F2. Partial reruns do not normalize every cell. See rerun_sensitivity.tsv for results without those substitutions. Repeated reads of run3 are not counted as another DB.", "",
             "| Workload | Baseline mean | Baseline min-max | F2Load latest | Difference | Baseline CV |",
             "|---|---:|---:|---:|---:|---:|"]
    for r in summary:
        lines.append(f'| {r["workload"]} | {r["baseline_mean"]:,.0f} | {r["baseline_min"]:,.0f}-{r["baseline_max"]:,.0f} | {r["f2load_latest"]:,.0f} | {r["f2_vs_mean_pct"]:+.2f}% | {r["baseline_cv_pct"]:.2f}% |')
    lines.extend(["", "Interpretation:", "",
                  "- A/B/F are close to the five-baseline mean; C also falls within baseline spread. D is 2.36% above the mean and only 0.29% above the best baseline. E is 11.96% above the mean and 8.79% above the best baseline. F2Load has one observation per workload; these are descriptive comparisons, not significance tests.",
                  "- Choosing only the paired run3 baseline makes C/D/E appear +4.24%/+5.83%/+15.13% faster. The five-DB comparison is -1.53%/+2.36%/+11.96%, respectively. Baseline choice materially changes the interpretation of C.",
                  "- C Get-found fraction is 60.2911% for run3, 60.2911-60.2924% over all five baselines, and 66.8923% for latest F2Load (+6.60 percentage points). Before F2Load was 62.8577%. Throughput proximity does not establish identical key membership or distribution; this metric is lookup success, not cache hit rate or a direct global distinct-key count.",
                  "- E scan work per operation is nearly identical for the paired baseline/F2Load: Next calls 47.9644/47.9680, iterator bytes 50088.34/50092.01, data-cache misses 9.0015/8.9962. Both have zero compaction writes in this cell. Fewer logical scan steps or cache misses therefore do not explain the entire observed difference. No causal claim about tree layout is established.",
                  "- Paired E seek p99 improves from 1844.51 to 1508.31 us (-18.23%). A read/write p99 increases 7.40%/4.33%, and F update p99 increases 5.86%; similar aggregate throughput does not imply identical tail latency. paired_p99.tsv keeps operation types separate and does not pool percentiles.",
                  "- The latest F2Load load bundle retains older loading fields and older log paths despite a new DB path. Its 134.73-second elapsed time must not be presented as the latest load measurement without resolving provenance. This report compares YCSB observations only.", "",
                  "Files: comparison.tsv (every baseline and summary), selected_cells.tsv (exact cell provenance), paired_p99.tsv, rerun_sensitivity.tsv, provenance.json (source hashes and configuration), comparison.png/pdf. Reproduce with python3 experiments/analysis/compare_five_baseline_ycsb.py."])
    (out / "REPORT.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines[6:16]))
    print("Output:", out)


if __name__ == "__main__":
    main()
