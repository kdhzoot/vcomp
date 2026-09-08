"""Native vertical TikZ bars for paper Figures 4 and 5, from promoted data."""

from decimal import Decimal, ROUND_HALF_UP
import math
from pathlib import Path


ACTIVE = "% Active vertical figure generated from the promoted TSV data."


def membership_percent(row):
    return float(row.get("successful_lookup_pct", 100 * float(row["bloom_true_positive_per_op"])))


def throughput_limits(reads, configs, systems):
    ratios = {(c, s): float(reads[(c, s)]["throughput_ops_sec"]) /
              float(reads[(c, "baseline")]["throughput_ops_sec"])
              for c in configs for s in systems}
    upper = max(3, math.ceil(max(ratios.values()) * 1.12))
    flush_bound = (math.floor(max(ratios[(c, "flush_only")] for c in configs) * 1000) + 1) / 1000
    return upper, flush_bound


def loading_label(value):
    # Preserve the manuscript's decimal rounding, e.g. 20.9500 -> 21.0 min.
    return str(Decimal(str(value)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def write_source(directory, stem, lines):
    path = Path(directory) / (stem + ".tex")
    previous = ""
    current = "\n".join(lines) + "\n"
    if path.exists():
        old = path.read_text()
        if ACTIVE in old:
            previous, active = old.split(ACTIVE + "\n", 1)
            if active != current:
                previous += "% Previous plotted version preserved for review:\n"
                previous += "\n".join("% " + line for line in active.splitlines()) + "\n\n"
        else:
            previous = "% Previous horizontal figure preserved for review:\n"
            previous += "\n".join("% " + line for line in old.splitlines()) + "\n\n"
    path.write_text(previous + ACTIVE + "\n" + current)


def picture(width, height, colors):
    lines = [r"\begingroup"]
    for index, color in enumerate(colors):
        lines.append(r"\definecolor{vb%d}{HTML}{%s}" % (index, color.lstrip("#")))
    lines += [
        r"\begin{tikzpicture}[x=1cm,y=1cm,font=\small,",
        r"axis/.style={draw=black!80,line width=0.5pt},",
        r"grid/.style={draw=black!16,line width=0.4pt}]",
        r"\path[use as bounding box] (0,0) rectangle (%.3f,%.3f);" % (width, height),
    ]
    return lines


def node(lines, x, y, text, options=""):
    lines.append(r"\node[font=\fontsize{7}{8}\selectfont,%s] at (%.4f,%.4f) {%s};"
                 % (options, x, y, text))


def rect(lines, left, bottom, right, top, color):
    lines.append(r"\draw[draw=black!80,line width=0.4pt,fill=vb%d] "
                 r"(%.5f,%.5f) rectangle (%.5f,%.5f);"
                 % (color, left, bottom, right, top))


def axes(lines, right, bottom, top, ticks, ylabel):
    for height, label in ticks:
        lines.append(r"\draw[grid] (0.85,%.4f) -- (%.4f,%.4f);"
                     % (height, right, height))
        lines.append(r"\draw[axis] (0.79,%.4f) -- (0.85,%.4f);" % (height, height))
        node(lines, 0.73, height, label, "anchor=east")
    lines += [r"\draw[axis] (0.85,%.4f) -- (%.4f,%.4f);" % (bottom, right, bottom),
              r"\draw[axis] (0.85,%.4f) -- (0.85,%.4f);" % (bottom, top)]
    node(lines, 0.10, (bottom + top) / 2, ylabel, "rotate=90,anchor=south")


def category(lines, x, bottom, label, rotation=55):
    node(lines, x, bottom - 0.10, label, "rotate=%d,anchor=north east" % rotation)


def finish(directory, stem, lines):
    lines += [r"\end{tikzpicture}", r"\endgroup"]
    write_source(directory, stem, lines)


def write_native_figures(directory, loading, reads, loading_order, systems,
                         loading_labels, system_labels, colors, configs,
                         config_colors, structural_config):
    Path(directory).mkdir(parents=True, exist_ok=True)
    bottom, top = 1.40, 4.70
    provenance = [
        "% Loading: vcomp/experiments/results/paper_figure4_loading_time_1tb_single.tsv",
        "% Reads: vcomp/experiments/results/paper_figure4_uniform_read_cache_5m_single.tsv",
        "% One run per cell; labels, membership and ratios derive from the TSV inputs.",
    ]

    lines = provenance + picture(8.5, 5.15, [colors[s] for s in loading_order])
    loading_upper = max(90, math.ceil(max(loading.values()) * 1.1 / 30) * 30)
    scale = lambda value: bottom + (top - bottom) * value / loading_upper
    axes(lines, 8.35, bottom, top, [(scale(t), str(t)) for t in range(0, loading_upper+1, 30)],
         "Loading time (min)")
    for i, system in enumerate(loading_order):
        x = 1.33 + 6.51 * i / max(1, len(loading_order) - 1)
        value = loading[system]
        lines.append("%% %s: %.8f min" % (system, value))
        rect(lines, x - 0.30, bottom, x + 0.30, scale(value), i)
        node(lines, x, scale(value) + 0.08, loading_label(value), "anchor=south")
        category(lines, x, bottom, loading_labels[system], 45)
    finish(directory, "bg_alternative_loading", lines)

    positions = [1.23 + 3.50 * i / max(1, len(systems) - 1) for i in range(len(systems))]
    palette = [colors[s] for s in systems]
    lines = provenance + picture(6.2, 5.65, palette)
    scale = lambda value: bottom + (top - bottom) * value / 5
    axes(lines, 5.13, bottom, top,
         [(scale(t), str(t)) for t in range(6)],
         "Filter checks / lookup")
    lines.append(r"\draw[axis] (5.13,%.4f) -- (5.13,%.4f);" % (bottom, top))
    for percent in (0, 25, 50, 75, 100):
        y = bottom + (top - bottom) * percent / 100
        lines.append(r"\draw[axis] (5.13,%.4f) -- (5.19,%.4f);" % (y, y))
        node(lines, 5.25, y, str(percent), "anchor=west")
    node(lines, 6.05, (bottom + top) / 2, r"Filter positive (\%)", "rotate=90,anchor=north")
    for i, (x, system) in enumerate(zip(positions, systems)):
        row = reads[(structural_config, system)]
        checks, positives = float(row["filter_probes_per_op"]), float(row["bloom_positive_per_op"])
        positive_percent = 100 * positives / checks
        lines.append("%% %s: checks=%.9f, positives=%.9f, filter_positive_percent=%.9f"
                     % (system, checks, positives, positive_percent))
        rect(lines, x - 0.22, bottom, x + 0.22, scale(min(checks, 5)), i)
        y = bottom + (top - bottom) * positive_percent / 100
        lines.append(r"\draw[draw=black,fill=white,line width=0.55pt] "
                     r"(%.4f,%.4f) -- (%.4f,%.4f) -- (%.4f,%.4f) -- (%.4f,%.4f) -- cycle;"
                     % (x, y + 0.09, x + 0.09, y, x, y - 0.09, x - 0.09, y))
        shown = "%.1fK" % (checks / 1000) if checks >= 1000 else "%.2f" % checks
        node(lines, x, scale(min(checks, 5)) + 0.07, shown, "anchor=south")
        if checks > 5:
            # Two strokes explicitly mark the clipped bar; retain its true value.
            for offset in (-0.07, 0.07):
                lines.append(r"\draw[white,line width=2.6pt] (%.4f,%.4f) -- (%.4f,%.4f);"
                             % (x - 0.25, top - 0.29 + offset, x + 0.25, top - 0.09 + offset))
                lines.append(r"\draw[black,line width=0.5pt] (%.4f,%.4f) -- (%.4f,%.4f);"
                             % (x - 0.25, top - 0.29 + offset, x + 0.25, top - 0.09 + offset))
        category(lines, x, bottom, system_labels[system])
    lines.append(r"\draw[draw=black,fill=black!35] (3.00,5.30) rectangle (3.26,5.46);")
    node(lines, 3.38, 5.38, "Filter checks", "anchor=west")
    lines.append(r"\draw[draw=black,fill=white] (3.13,5.17) -- (3.22,5.08) -- (3.13,4.99) -- (3.04,5.08) -- cycle;")
    node(lines, 3.38, 5.08, "Filter positive", "anchor=west")
    finish(directory, "bg_alternative_lookup_work", lines)

    lines = provenance + picture(5.2, 5.65, palette)
    scale = lambda value: bottom + (top - bottom) * value / 115
    axes(lines, 5.13, bottom, top, [(scale(t), str(t)) for t in (0, 50, 100)],
         r"Successful lookups (\%)")
    for i, (x, system) in enumerate(zip(positions, systems)):
        value = membership_percent(reads[(structural_config, system)])
        lines.append("%% %s: successful lookups=%.9f%%" % (system, value))
        rect(lines, x - 0.22, bottom, x + 0.22, scale(value), i)
        shown = "100" if value >= 99.99 else "%.1f" % value
        node(lines, x, scale(value) + 0.07, shown, "anchor=south")
        category(lines, x, bottom, system_labels[system])
    finish(directory, "bg_alternative_lookup_hits", lines)

    lines = provenance + picture(7.2, 5.65, config_colors)
    throughput_upper, flush_bound = throughput_limits(reads, configs, systems)
    scale = lambda value: bottom + (top - bottom) * value / throughput_upper
    axes(lines, 7.10, bottom, top,
         [(scale(t), str(t)) for t in range(throughput_upper+1)],
         "Throughput / Baseline")
    for i, system in enumerate(systems):
        x = 1.40 + 5.20 * i / max(1, len(systems) - 1)
        for j, config in enumerate(configs):
            qps = float(reads[(config, system)]["throughput_ops_sec"])
            baseline_qps = float(reads[(config, "baseline")]["throughput_ops_sec"])
            ratio = qps / baseline_qps
            center = x + (j - 1.5) * 0.19
            lines.append("%% %s / %s: %.6f ops/s, baseline=%.6f ops/s, normalized=%.9f"
                         % (system, config, qps, baseline_qps, ratio))
            rect(lines, center - 0.085, bottom, center + 0.085, scale(ratio), j)
        if system == "flush_only":
            node(lines, x, scale(0.13), r"$<%.3f$" % flush_bound, "anchor=south")
        category(lines, x, bottom, system_labels[system])
    lines.append(r"\draw[red,line width=0.8pt] (0.85,%.4f) -- (7.10,%.4f);"
                 % (scale(1), scale(1)))
    for j, label in enumerate([r"Cached, $\approx$0", r"Pinned, $\approx$0",
                                "Cached, 50 GiB", "Pinned, 50 GiB"]):
        x, y = 1.40 + 2.75 * (j % 2), 5.38 - 0.30 * (j // 2)
        rect(lines, x, y - 0.08, x + 0.23, y + 0.08, j)
        node(lines, x + 0.34, y, label, "anchor=west")
    finish(directory, "bg_alternative_read_throughput", lines)
