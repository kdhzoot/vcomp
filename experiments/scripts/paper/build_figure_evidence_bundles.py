#!/usr/bin/env python3
"""Build copy-only evidence bundles for the empirical figures in the paper."""

from __future__ import annotations

import csv
import hashlib
import shutil
from pathlib import Path


EXPERIMENTS = Path(__file__).resolve().parents[2]
REPO = EXPERIMENTS.parent
WORKSPACE = REPO.parent
PAPER = WORKSPACE / "paper"
ARTIFACTS = EXPERIMENTS / "artifacts"
RESULTS = EXPERIMENTS / "results"
OUT = EXPERIMENTS / "paper_evidence" / "current"


def read_tsv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def write_tsv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Bundle:
    def __init__(self, bundle_id: str):
        self.bundle_id = bundle_id
        self.root = OUT / bundle_id
        self.root.mkdir(parents=True, exist_ok=True)
        self.sources: list[dict[str, object]] = []

    def copy(self, source: Path, category: str = "source_logs") -> None:
        if not source.exists() or not source.is_file():
            self.sources.append({
                "bundle_path": "NA",
                "original_path": str(source.relative_to(WORKSPACE)) if source.is_absolute() else str(source),
                "bytes": "NA",
                "sha256": "NA",
                "status": "missing",
            })
            return
        try:
            relative = source.relative_to(WORKSPACE)
        except ValueError:
            relative = Path(source.name)
        destination = self.root / category / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            destination.unlink()
        shutil.copy2(source, destination)
        self.sources.append({
            "bundle_path": str(destination.relative_to(self.root)),
            "original_path": str(relative),
            "bytes": source.stat().st_size,
            "sha256": sha256(source),
            "status": "copied",
        })

    def copy_tree(self, source: Path, *, include_large_compaction_trace: bool = False) -> None:
        allowed_names = {
            "summary.tsv", "validated_summary.tsv", "q3_read_summary.tsv", "run.log",
            "series.log", "bench.out", "stdout.txt", "stderr.txt", "report.rep",
            "time.out", "load_cmd.sh", "fillseq_cmd.sh", "overwrite_cmd.sh",
            "compact_cmd.sh", "reopen_cmd.sh", "elapsed_sec.txt", "exit_code.txt",
            "manifest.tsv", "TERMINATED_AFTER_ONE_REP.txt",
            "run_cmd.sh", "manifest.txt", "db_bench.sha256",
            "vcomp-prof.commit", "vcomp-prof.status",
            "compaction_jobs.csv", "compaction_breakdown.tsv", "compaction_breakdown_all.tsv",
            "compaction_breakdown_representative.tsv", "diskstats.start", "diskstats.end",
            "start_epoch.txt", "end_epoch.txt", "validation.log", "reopen_validation.out",
        }
        if include_large_compaction_trace:
            allowed_names.add("compaction_breakdown.raw")
        if not source.exists():
            self.copy(source)
            return
        for path in sorted(source.rglob("*")):
            if path.is_file() and (path.name in allowed_names or "provenance" in path.parts):
                self.copy(path)

    def copy_asset(self, stem: str) -> None:
        for suffix in (".pdf", ".png", ".tex"):
            path = PAPER / "figs" / f"{stem}{suffix}"
            if path.exists():
                self.copy(path, "figure_assets")

    def finish(self) -> None:
        write_tsv(
            self.root / "provenance.tsv",
            self.sources,
            ["bundle_path", "original_path", "bytes", "sha256", "status"],
        )


def build_background_loading() -> None:
    bundle = Bundle("fig_bg_loading")
    bundle.copy_asset("bg_loading_scale_redraw")
    bundle.copy_asset("bg_loading_breakdown_redraw")

    scale_path = ARTIFACTS / "figure2_true91_single_run_260831/source_snapshots/figure2a_loading_time.tsv"
    breakdown_path = RESULTS / "paper_background_compaction_breakdown.tsv"
    rows: list[dict[str, object]] = []
    for row in read_tsv(scale_path):
        rows.append({
            "panel": "a", "series": row["series"], "dataset_gib": row["dataset_GiB"],
            "category": "Loading time", "value": row["elapsed_hour"], "unit": "hour",
            "point_type": row["point_type"], "source": row["source"],
        })
    for row in read_tsv(breakdown_path):
        rows.append({
            "panel": "b", "series": row["series"], "dataset_gib": "500",
            "category": row["category"], "value": row["time_hour"], "unit": "hour",
            "point_type": row["status"], "source": row["source"],
        })
    write_tsv(bundle.root / "displayed_values.tsv", rows,
              ["panel", "series", "dataset_gib", "category", "value", "unit", "point_type", "source"])
    bundle.copy(scale_path, "source_data")
    bundle.copy(breakdown_path, "source_data")

    one_kb = ARTIFACTS / "log_loads/motivation_load_scaling_260602_cleanrocksdb_motivation"
    true_91 = ARTIFACTS / "log_loads/exp_260604_exp91b_baseline/scaling_91b_none"
    direct_8tb = ARTIFACTS / "log_loads/exp_260822_paper_bg_91b_8tb_direct"
    breakdown = ARTIFACTS / "log_loads/paper_bg_breakdown_260831_034107"
    bundle.copy_tree(one_kb)
    bundle.copy_tree(true_91)
    bundle.copy_tree(direct_8tb)
    bundle.copy_tree(breakdown, include_large_compaction_trace=True)
    bundle.finish()


def build_compaction_alternatives() -> None:
    bundle_root = OUT / "fig_bg_compaction_alternatives"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    bundle = Bundle("fig_bg_compaction_alternatives")
    bundle.copy(PAPER / "figs/bg_alternative_loading.tex", "figure_assets")
    source = RESULTS / "paper_figure4_loading_time_1tb_single.tsv"
    rows = read_tsv(source)
    write_tsv(bundle.root / "displayed_values.tsv", rows, list(rows[0].keys()))
    bundle.copy(source, "source_data")
    bundle.copy(
        EXPERIMENTS / "analysis/plot_paper_figure4_uniform_read_cache.py",
        "reproduction",
    )
    for directory in (
        ARTIFACTS / "log_loads/paper_clean_vector_wb16_1000gib_260903_run1",
        ARTIFACTS / "log_loads/paper_adoc_wb16_1000gib_260903_run1",
        ARTIFACTS / "log_loads/paper_blobdb_wb16_1000gib_260903_run1/blobdb_1000gib",
        ARTIFACTS / "log_loads/paper_lastcomp_flushonly_wb16_1tib_260902_run1",
        ARTIFACTS / "log_loads/paper_lastcomp_from_nocomp_wb16_1000gib_260903_run1/lastcomp_1000gib",
        ARTIFACTS / "log_loads/paper_clean_fillseq_wb16_1000gib_260903_run1/fillseq_1000gib",
        ARTIFACTS / "log_loads/paper_clean_fillseq_overwrite_wb16_1000gib_260903_run1/fillseq_overwrite_1000gib",
        ARTIFACTS / "log_loads/motivation_vcomp_speedup_1kb_260609_vcomp1kb_reload/vcomp_1000gb_1kb_none",
    ):
        bundle.copy_tree(directory)
    bundle.finish()


def build_background_alternative_reads() -> None:
    bundle_root = OUT / "fig_bg_alternative_reads"
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    bundle = Bundle("fig_bg_alternative_reads")
    for stem in (
        "bg_alternative_lookup_work",
        "bg_alternative_lookup_hits",
        "bg_alternative_read_throughput",
    ):
        bundle.copy(PAPER / f"figs/{stem}.tex", "figure_assets")

    summary = RESULTS / "paper_figure4_uniform_read_cache_5m_single.tsv"
    summary_rows = read_tsv(summary)
    displayed: list[dict[str, object]] = []
    source_name = str(summary.relative_to(WORKSPACE))
    structural_config = "D_pinned_5pct"
    for row in summary_rows:
        common = {
            "config_id": row["config_id"],
            "config_label": row["config_label"],
            "system": row["system"],
            "system_label": row["system_label"],
            "point_type": "single_run",
            "source": source_name,
        }
        if row["config_id"] == structural_config:
            displayed.extend(
                [
                    {
                        "panel": "a",
                        **common,
                        "metric": "filter_probes_per_op",
                        "value": row["filter_probes_per_op"],
                        "unit": "checks/op",
                    },
                    {
                        "panel": "a",
                        **common,
                        "metric": "bloom_positive_per_op",
                        "value": row["bloom_positive_per_op"],
                        "unit": "positives/op",
                    },
                    {
                        "panel": "b",
                        **common,
                        "metric": "successful_lookup_pct",
                        "value": (
                            f"{100 * float(row['bloom_true_positive_per_op']):.9f}"
                        ),
                        "unit": "percent",
                    },
                ]
            )
        displayed.append(
            {
                "panel": "c",
                **common,
                "metric": "throughput",
                "value": row["throughput_ops_sec"],
                "unit": "ops/sec",
            }
        )
    write_tsv(
        bundle.root / "displayed_values.tsv",
        displayed,
        [
            "panel", "config_id", "config_label", "system", "system_label",
            "metric", "value", "unit", "point_type", "source",
        ],
    )
    bundle.copy(summary, "source_data")

    for path in (
        ARTIFACTS / "log_loads/paper_figure4_read_db_matrix.tsv",
        ARTIFACTS / "log_loads/paper_figure4_read_baseline_matrix.tsv",
    ):
        bundle.copy(path, "source_data")
    for directory in (
        ARTIFACTS
        / "log_runs/paper_figure4_uniform_read_cache_260904_1115_uniform_cache_5m",
        ARTIFACTS
        / "log_runs/paper_figure4_uniform_read_baseline_260905_5m",
    ):
        bundle.copy_tree(directory)
    for path in (
        EXPERIMENTS / "analysis/summarize_paper_figure4_uniform_read_cache.py",
        EXPERIMENTS / "analysis/plot_paper_figure4_uniform_read_cache.py",
        EXPERIMENTS / "scripts/read/run_paper_figure4_uniform_cache_matrix.sh",
        EXPERIMENTS / "scripts/read/run_q3_read_workloads.sh",
        EXPERIMENTS / "docs/PAPER_FIGURE4_UNIFORM_READ_CACHE_MATRIX.md",
    ):
        bundle.copy(path, "reproduction")
    bundle.finish()


def build_eval_speedup() -> None:
    bundle = Bundle("fig_eval_speedup")
    bundle.copy_asset("eval_speedup")
    one_kb = read_tsv(ARTIFACTS / "log_loads/motivation_speedup_latest.tsv")
    speedup_91 = {row["size_gb"]: row for row in read_tsv(
        ARTIFACTS / "log_loads/motivation_vcomp_speedup_91b_260610/speedup_91b_summary.tsv")}
    rows: list[dict[str, object]] = []
    for row in one_kb:
        if row["kv"] != "1KB":
            continue
        for system, seconds, source in (
            ("baseline", row["baseline_elapsed_sec"], row["baseline_summary"]),
            ("F2Load", row["vcomp_elapsed_sec"], row["vcomp_summary"]),
        ):
            rows.append({
                "kv": "1KB", "dataset_gib": row["size_gb"], "system": system,
                "loading_sec": seconds, "loading_hour": f"{float(seconds) / 3600:.9f}",
                "point_type": "measured", "figure_annotation_min":
                    f"{float(seconds) / 60:.0f}" if system == "F2Load" else "",
                "source": source, "audit_note": "",
            })
    baseline_91 = {row["dataset_GiB"]: row for row in read_tsv(
        ARTIFACTS / "figure2_true91_single_run_260831/source_snapshots/figure2a_loading_time.tsv")
        if row["series"] == "91B"}
    for size in ("500", "1000", "2000", "4000", "8000"):
        if size == "8000":
            rows.append({
                "kv": "91B", "dataset_gib": size, "system": "baseline",
                "loading_sec": 169200, "loading_hour": 47, "point_type": "projected",
                "figure_annotation_min": "", "source": "trend from measured 500-4000 GiB runs",
                "audit_note": "Current paper asset is hatched and manuscript states 47 hours.",
            })
        else:
            row = baseline_91[size]
            rows.append({
                "kv": "91B", "dataset_gib": size, "system": "baseline",
                "loading_sec": row["elapsed_sec"], "loading_hour": row["elapsed_hour"],
                "point_type": "measured", "figure_annotation_min": "", "source": row["source"],
                "audit_note": "",
            })
        vrow = speedup_91[size]
        annotation = "103" if size == "8000" else f"{float(vrow['fillvirtual_sec']) / 60:.0f}"
        note = ""
        if size == "8000":
            note = ("Paper PNG annotates 103 min, but the retained 260610 run is 6830.974 s "
                    "(113.85 min); exact source log for the 103-min annotation is not identified.")
        rows.append({
            "kv": "91B", "dataset_gib": size, "system": "F2Load",
            "loading_sec": vrow["fillvirtual_sec"],
            "loading_hour": f"{float(vrow['fillvirtual_sec']) / 3600:.9f}",
            "point_type": "measured_candidate" if size == "8000" else "measured",
            "figure_annotation_min": annotation,
            "source": "experiments/artifacts/log_loads/motivation_vcomp_speedup_91b_260610/speedup_91b_summary.tsv",
            "audit_note": note,
        })
    write_tsv(bundle.root / "displayed_values.tsv", rows,
              ["kv", "dataset_gib", "system", "loading_sec", "loading_hour", "point_type",
               "figure_annotation_min", "source", "audit_note"])
    for path in (
        ARTIFACTS / "log_loads/motivation_speedup_latest.tsv",
        ARTIFACTS / "log_loads/speedup_writeamp_all.tsv",
        ARTIFACTS / "log_loads/motivation_vcomp_speedup_91b_260610/speedup_91b_summary.tsv",
    ):
        bundle.copy(path, "source_data")
    for directory in (
        ARTIFACTS / "log_loads/motivation_load_scaling_260602_cleanrocksdb_motivation",
        ARTIFACTS / "log_loads/exp_260604_exp91b_baseline/scaling_91b_none",
        ARTIFACTS / "log_loads/motivation_vcomp_speedup_1kb_260609_vcomp1kb_reload",
        ARTIFACTS / "log_loads/motivation_vcomp_speedup_91b_260610",
    ):
        bundle.copy_tree(directory)
    bundle.finish()


def build_eval_accuracy() -> None:
    bundle = Bundle("fig_eval_vcomp")
    bundle.copy_asset("eval_vcomp_sst_count")
    bundle.copy_asset("eval_vcomp_sst_byte")
    jobs_path = ARTIFACTS / "log_loads/vcomp_accuracy_kmv512_500gb_jobs.tsv"
    rows = []
    for row in read_tsv(jobs_path):
        rows.append({
            "job": row["job"], "output_level": row["output_level"],
            "actual_outputs": row["actual_outputs"], "predicted_outputs": row["predicted_outputs"],
            "actual_bytes": row["actual_bytes"], "predicted_bytes": row["predicted_bytes"],
            "absolute_output_size_error_pct":
                f"{abs(float(row['predicted_bytes']) - float(row['actual_bytes'])) / float(row['actual_bytes']) * 100:.9f}",
        })
    write_tsv(bundle.root / "displayed_values.tsv", rows,
              ["job", "output_level", "actual_outputs", "predicted_outputs", "actual_bytes",
               "predicted_bytes", "absolute_output_size_error_pct"])
    for path in (
        jobs_path,
        ARTIFACTS / "log_loads/vcomp_accuracy_kmv512_processed_summary.tsv",
        ARTIFACTS / "log_loads/vcomp_accuracy_kmv512_processed_by_level.tsv",
    ):
        bundle.copy(path, "source_data")
    bundle.copy_tree(ARTIFACTS / "log_loads/acc_500gb_1kb_none_kmv_globaltotal_k512_b8_260608_124232")
    bundle.finish()


def build_eval_scale() -> None:
    bundle = Bundle("fig_exp_scale")
    for stem in ("exp_scale_throughput", "exp_scale_latency", "exp_scale_disk_read", "exp_scale_disk_write"):
        bundle.copy_asset(stem)
    full_path = ARTIFACTS / "coverage_dumps/read10tb_realistic_full.tsv"
    rows = []
    metrics = (
        ("throughput", "throughput_ops_sec", "ops/sec"),
        ("latency_p50", "p50_us", "us"),
        ("latency_p95", "p95_us", "us"),
        ("latency_p99", "p99_us", "us"),
        ("disk_read", "disk_read_mb", "MiB"),
        ("disk_write", "disk_write_mb", "MiB"),
    )
    for row in read_tsv(full_path):
        for metric, column, unit in metrics:
            if metric.startswith("latency") and row["workload"] == "workloade":
                continue
            rows.append({
                "workload": row["workload"], "system": row["system"], "metric": metric,
                "value": row[column], "unit": unit, "source_result_dir": row["result_dir"],
            })
    write_tsv(bundle.root / "displayed_values.tsv", rows,
              ["workload", "system", "metric", "value", "unit", "source_result_dir"])
    for path in (full_path, ARTIFACTS / "coverage_dumps/read10tb_realistic_compare.tsv"):
        bundle.copy(path, "source_data")
    for name in ("ab", "c", "de", "fmix"):
        bundle.copy_tree(ARTIFACTS / f"log_runs/q3_flex_read10tb_realistic_{name}_260610")
    bundle.finish()


def build_eval_sweep() -> None:
    bundle = Bundle("fig_eval_sweep")
    bundle.copy_asset("eval_sweep")
    loading_path = ARTIFACTS / "coverage_dumps/sweep_loading_metrics.tsv"
    read_path = ARTIFACTS / "coverage_dumps/sweep_ycsbc_compare.tsv"
    loading = {row["case_id"]: row for row in read_tsv(loading_path)}
    reads = {row["case_id"]: row for row in read_tsv(read_path)}
    rows = []
    for case_id in sorted(loading):
        load = loading[case_id]
        read = reads[case_id]
        metrics = (
            ("DB size", load["base_db_GB"], load["vcomp_db_GB"]),
            ("SST count", load["base_sst_count"], load["vcomp_sst_count"]),
            ("SST size", load["base_avg_sst_MB"], load["vcomp_avg_sst_MB"]),
            ("Throughput", read["base_throughput_ops_sec"], read["vcomp_throughput_ops_sec"]),
            ("Avg latency", read["base_avg_latency_us"], read["vcomp_avg_latency_us"]),
            ("Disk read", read["base_disk_read_mb"], read["vcomp_disk_read_mb"]),
            ("Disk rIOPS", read["base_disk_read_ios"], read["vcomp_disk_read_ios"]),
        )
        for metric, baseline, vcomp in metrics:
            rows.append({
                "case_id": case_id, "dataset_gib": load["size_gb"], "kv": load["kv"],
                "distribution": load["dist"], "metric": metric, "baseline_value": baseline,
                "f2load_value": vcomp, "normalized_f2load_over_baseline":
                    f"{float(vcomp) / float(baseline):.9f}",
            })
    write_tsv(bundle.root / "displayed_values.tsv", rows,
              ["case_id", "dataset_gib", "kv", "distribution", "metric", "baseline_value",
               "f2load_value", "normalized_f2load_over_baseline"])
    for path in (
        loading_path, read_path, ARTIFACTS / "log_loads/q3_read_db_matrix_260609_reload_with_load_stats.tsv",
    ):
        bundle.copy(path, "source_data")
    bundle.copy_tree(ARTIFACTS / "log_runs/q3_flex_read_sweep_realistic_c_260610")
    bundle.copy_tree(ARTIFACTS / "log_loads/trace_baseline_after_91b_snappy_260606_1851")
    bundle.copy_tree(ARTIFACTS / "log_loads/trace_vcomp_kmv512_q3_reload_260609_043934")
    bundle.finish()


def build_index() -> None:
    rows = [
        {"paper_label": "fig:LSM-Tree", "bundle": "NA", "kind": "conceptual", "status": "no numeric data"},
        {"paper_label": "fig:bg-loading (+ scale/breakdown subfigures)", "bundle": "fig_bg_loading", "kind": "empirical", "status": "bundled"},
        {"paper_label": "fig:bg-naive-lsm-states", "bundle": "NA", "kind": "conceptual", "status": "no numeric data"},
        {"paper_label": "fig:bg-compaction-alternatives", "bundle": "fig_bg_compaction_alternatives", "kind": "empirical", "status": "bundled"},
        {"paper_label": "fig:bg-alternative-reads (+ lookup-work/hit/throughput subfigures)", "bundle": "fig_bg_alternative_reads", "kind": "empirical", "status": "bundled; single run per configuration"},
        {"paper_label": "fig:ds-overview", "bundle": "NA", "kind": "conceptual", "status": "no numeric data"},
        {"paper_label": "fig:ds-learned-index (+ model/merge subfigures)", "bundle": "NA", "kind": "conceptual/example", "status": "no experiment data"},
        {"paper_label": "fig:eval-speedup", "bundle": "fig_eval_speedup", "kind": "empirical", "status": "bundled; 8 TiB 91B annotation source mismatch flagged"},
        {"paper_label": "fig:eval-vcomp (+ count/byte subfigures)", "bundle": "fig_eval_vcomp", "kind": "empirical", "status": "bundled"},
        {"paper_label": "fig:exp-scale (+ throughput/latency/read/write subfigures)", "bundle": "fig_exp_scale", "kind": "empirical", "status": "bundled"},
        {"paper_label": "fig:eval-sweep", "bundle": "fig_eval_sweep", "kind": "empirical", "status": "bundled"},
    ]
    write_tsv(OUT / "index.tsv", rows, ["paper_label", "bundle", "kind", "status"])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(EXPERIMENTS / "docs/PAPER_FIGURE_EVIDENCE_BUNDLES.md", OUT / "README.md")
    build_background_loading()
    build_compaction_alternatives()
    build_background_alternative_reads()
    build_eval_speedup()
    build_eval_accuracy()
    build_eval_scale()
    build_eval_sweep()
    build_index()
    print(OUT)


if __name__ == "__main__":
    main()
