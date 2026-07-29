#!/usr/bin/env python3
"""Visualize a single compaction trace job.

Usage:
    python3 viz_compaction.py <trace_log_file> [--out <output.png>]
"""

import argparse
import re
import sys

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.ticker import FuncFormatter
import numpy as np


# ── Parsing ──────────────────────────────────────────────────────────────────

def parse_trace(path):
    metadata = {}
    input_files = []
    output_files = []
    file_by_number = {}

    with open(path) as f:
        lines = [l.rstrip("\n") for l in f]

    STATE_META, STATE_INPUT, STATE_OUTPUT, STATE_KEYDUMP, STATE_SUMMARY = range(5)
    state = STATE_META
    cur_file = None
    cur_level = 0
    cur_dump_fnum = None
    cur_dump_keys = []
    target_list = input_files

    re_level = re.compile(r"^Level\s+(\d+)\s+\((\d+) files?\):")
    re_file = re.compile(r"^\s{2}File #(\d+)$")
    re_output_file = re.compile(r"^\s{4}File #(\d+)$")
    re_keydump = re.compile(r"^\s+--- File #(\d+) \(L(\d+)\) Keys ---")
    re_key = re.compile(r"^\s+\[(\d+)\]\s+(\w+)\s+\|\s+([0-9A-Fa-f]+)")
    re_keyfooter = re.compile(r"^\s+Total Keys in File:\s+(\d+)")
    re_outlevel = re.compile(r"^\s+Output Level L(\d+):")

    def flush_file():
        nonlocal cur_file
        if cur_file:
            target_list.append(cur_file)
            file_by_number[cur_file["file_number"]] = cur_file
            cur_file = None

    def flush_dump():
        nonlocal cur_dump_fnum, cur_dump_keys
        if cur_dump_fnum is not None and cur_dump_fnum in file_by_number:
            file_by_number[cur_dump_fnum]["keys"] = cur_dump_keys
        cur_dump_fnum = None
        cur_dump_keys = []

    for line in lines:
        if line == "--- INPUT FILES ---":
            state = STATE_INPUT; target_list = input_files; continue
        if line == "--- OUTPUT FILES ---":
            flush_file(); state = STATE_OUTPUT; target_list = output_files; continue
        if line == "--- COMPACTION SUMMARY ---":
            flush_file(); flush_dump(); state = STATE_SUMMARY; continue

        m = re_keydump.match(line)
        if m:
            flush_file(); flush_dump()
            cur_dump_fnum = int(m.group(1)); cur_dump_keys = []
            state = STATE_KEYDUMP; continue

        if state == STATE_KEYDUMP:
            m = re_key.match(line)
            if m: cur_dump_keys.append(m.group(3)); continue
            m = re_keyfooter.match(line)
            if m: flush_dump(); continue
            if line.strip() and not line.startswith("  ["): flush_dump()
            else: continue

        if state == STATE_META:
            m = re.match(r"^([\w\s()]+):\s*(.+)$", line)
            if m: metadata[m.group(1).strip()] = m.group(2).strip()
            continue

        if state == STATE_INPUT:
            m = re_level.match(line)
            if m: flush_file(); cur_level = int(m.group(1)); continue
            m = re_file.match(line)
            if m:
                flush_file()
                cur_file = {"file_number": int(m.group(1)), "level": cur_level, "keys": []}
                continue
            if cur_file and line.startswith("    "):
                _parse_prop(cur_file, line.strip())
            continue

        if state == STATE_OUTPUT:
            m = re_outlevel.match(line)
            if m: flush_file(); cur_level = int(m.group(1)); continue
            m = re_output_file.match(line)
            if m:
                flush_file()
                cur_file = {"file_number": int(m.group(1)), "level": cur_level, "keys": []}
                continue
            if cur_file and line.startswith("      "):
                _parse_prop(cur_file, line.strip())
            continue

    flush_file(); flush_dump()
    return metadata, input_files, output_files


def _parse_prop(f, line):
    if line.startswith("Size:"):
        m = re.search(r"(\d+)", line); f["size"] = int(m.group(1)) if m else 0
    elif line.startswith("Smallest Key:"):
        m = re.search(r"Smallest Key:\s*(\S+)", line); f["smallest"] = m.group(1) if m else ""
    elif line.startswith("Largest Key:"):
        m = re.search(r"Largest Key:\s*(\S+)", line); f["largest"] = m.group(1) if m else ""
    elif line.startswith("Entries:"):
        m = re.search(r"(\d+)", line); f["entries"] = int(m.group(1)) if m else 0


# ── Visualization ────────────────────────────────────────────────────────────

def key_int(h):
    """First 8 bytes (16 hex chars) as integer."""
    return int(h[:16], 16)


def make_figure(metadata, input_files, output_files, out_path):
    all_files = input_files + output_files
    if not all_files:
        print("No files found.", file=sys.stderr); return

    # Compute global key range
    vals = []
    for f in all_files:
        if "smallest" in f: vals.append(key_int(f["smallest"]))
        if "largest" in f: vals.append(key_int(f["largest"]))
    if not vals:
        print("No keys found.", file=sys.stderr); return

    g_min, g_max = min(vals), max(vals)
    g_span = g_max - g_min or 1

    # Normalize to [0, 1] for clean layout
    def N(v): return (v - g_min) / g_span

    # Row layout
    input_levels = sorted(set(f.get("level", 0) for f in input_files))
    output_levels = sorted(set(f.get("level", 0) for f in output_files))
    gap = 1.2
    n_in = len(input_levels) or 1
    n_out = len(output_levels) or 1
    total_h = n_in + gap + n_out

    in_row = {lvl: total_h - 1 - i for i, lvl in enumerate(input_levels)}
    out_row = {lvl: n_out - 1 - i for i, lvl in enumerate(output_levels)}

    colors = {0: "#3B7DD8", 1: "#E8913A", 2: "#D94745", 3: "#5BB5A8",
              4: "#48A340", 5: "#D9C23A", 6: "#A06DAA"}

    fig_h = max(6, total_h * 2.5)
    fig, ax = plt.subplots(figsize=(20, fig_h))

    bar_h = 0.65

    def draw(finfo, row):
        if "smallest" not in finfo or "largest" not in finfo:
            return
        lvl = finfo.get("level", 0)
        c = colors.get(lvl, "#888888")

        xl = N(key_int(finfo["smallest"]))
        xr = N(key_int(finfo["largest"]))
        w = max(xr - xl, 0.005)

        # Outline only
        ax.add_patch(plt.Rectangle(
            (xl, row - bar_h / 2), w, bar_h,
            facecolor="none", edgecolor=c, linewidth=1.5))

        # Key distribution
        keys = finfo.get("keys", [])
        if keys:
            kv = np.array([N(key_int(k)) for k in keys])

            # # --- Histogram mode ---
            # nbins = min(120, max(10, len(keys) // 40))
            # bins = np.linspace(xl, xr, nbins + 1)
            # counts, edges = np.histogram(kv, bins=bins)
            # if counts.max() > 0:
            #     h_norm = counts / counts.max() * (bar_h * 0.9)
            #     for j in range(len(counts)):
            #         if counts[j] == 0: continue
            #         ax.add_patch(plt.Rectangle(
            #             (edges[j], row - h_norm[j] / 2),
            #             edges[j+1] - edges[j], h_norm[j],
            #             facecolor=c, edgecolor="none", alpha=0.7))

            # --- CDF mode ---
            kv_sorted = np.sort(kv)
            cdf_y = np.linspace(0, 1, len(kv_sorted))
            # Map CDF [0,1] to bar vertical range
            cdf_plot_y = row - bar_h / 2 + cdf_y * bar_h
            ax.plot(kv_sorted, cdf_plot_y, color=c, linewidth=1.5, alpha=0.85)
            ax.fill_betweenx(cdf_plot_y, xl, kv_sorted,
                             color=c, alpha=0.25)

        # Label
        fnum = finfo["file_number"]
        nk = len(keys)
        mb = finfo.get("size", 0) / (1024 * 1024)
        ax.text(xl + w / 2, row, f"#{fnum}\n{nk:,} keys\n{mb:.1f}MB",
                ha="center", va="center", fontsize=10, fontweight="bold",
                color="white",
                bbox=dict(boxstyle="round,pad=0.15", fc="black", alpha=0.6))

    for f in input_files:
        draw(f, in_row[f.get("level", 0)])
    for f in output_files:
        draw(f, out_row[f.get("level", 0)])

    # Separator
    sep_y = n_out - 0.5 + gap * 0.5
    ax.axhline(y=sep_y, color="#888888", ls="--", lw=0.8, alpha=0.6)

    # Y labels
    yt, yl = [], []
    for lvl in input_levels:  yt.append(in_row[lvl]);  yl.append(f"Input L{lvl}")
    for lvl in output_levels: yt.append(out_row[lvl]); yl.append(f"Output L{lvl}")
    ax.set_yticks(yt)
    ax.set_yticklabels(yl, fontsize=20, fontweight="bold")
    ax.set_ylim(-0.7, total_h + 0.2)

    # X axis — actual key values as hex
    ax.set_xlim(-0.03, 1.03)
    nticks = 10
    tick_vals = np.linspace(0, 1, nticks + 1)
    tick_labels = [f"0x{int(g_min + t * g_span):X}" for t in tick_vals]
    ax.set_xticks(tick_vals)
    ax.set_xticklabels(tick_labels, fontsize=14, rotation=30, ha="right")
    ax.set_xlabel("Key (first 8 bytes)", fontsize=20)

    # Title
    jid = metadata.get("Job ID", "?")
    reason = metadata.get("Compaction Reason", "?")
    ilvl = metadata.get("Input Level(s)", "?")
    olvl = metadata.get("Output Level", "?")
    t_in = sum(f.get("size", 0) for f in input_files) / (1024**2)
    t_out = sum(f.get("size", 0) for f in output_files) / (1024**2)
    nk_in = sum(len(f.get("keys", [])) for f in input_files)
    nk_out = sum(len(f.get("keys", [])) for f in output_files)

    ax.set_title(
        f"Compaction Job #{jid}  |  {reason}  |  {ilvl} → L{olvl}\n"
        f"Input: {len(input_files)} files, {nk_in:,} keys, {t_in:.1f}MB  →  "
        f"Output: {len(output_files)} files, {nk_out:,} keys, {t_out:.1f}MB  "
        f"(Δkeys: {nk_out - nk_in:+,})",
        fontsize=20, fontweight="bold", pad=18)

    # Legend
    used = sorted(set(f.get("level", 0) for f in all_files))
    ax.legend(handles=[mpatches.Patch(color=colors.get(l, "#888888"), label=f"L{l}") for l in used],
              loc="upper right", fontsize=16)

    ax.grid(axis="x", alpha=0.2, lw=0.5)
    ax.set_axisbelow(True)
    fig.patch.set_facecolor("white")
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, bbox_inches="tight") if out_path else plt.show()
    print(f"Saved: {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("trace_file")
    ap.add_argument("--out", "-o")
    args = ap.parse_args()

    md, inf, outf = parse_trace(args.trace_file)
    print(f"Job #{md.get('Job ID','?')}: {len(inf)} input, {len(outf)} output files")
    for f in inf:  print(f"  In  L{f.get('level','?')} #{f['file_number']}: {len(f.get('keys',[]))} keys")
    for f in outf: print(f"  Out L{f.get('level','?')} #{f['file_number']}: {len(f.get('keys',[]))} keys")

    # Deduplication analysis
    in_keys = set()
    for f in inf:
        for k in f.get("keys", []):
            in_keys.add(k[:16])  # first 8 bytes
    out_keys = set()
    for f in outf:
        for k in f.get("keys", []):
            out_keys.add(k[:16])

    deduped = in_keys - out_keys
    n_in = sum(len(f.get("keys", [])) for f in inf)
    n_out = sum(len(f.get("keys", [])) for f in outf)

    print(f"\n--- Deduplication ---")
    print(f"  Input keys (total):  {n_in:,}")
    print(f"  Output keys (total): {n_out:,}")
    print(f"  Unique input keys:   {len(in_keys):,}")
    print(f"  Unique output keys:  {len(out_keys):,}")
    print(f"  Deduplicated:        {n_in - n_out:,} ({(n_in - n_out) / n_in * 100:.2f}%)" if n_in else "")
    print(f"  Dropped unique keys: {len(deduped):,}")
    if deduped:
        sorted_deduped = sorted(deduped)
        show = min(20, len(sorted_deduped))
        print(f"  First {show} dropped keys:")
        for k in sorted_deduped[:show]:
            print(f"    0x{k}")
        if len(sorted_deduped) > show:
            print(f"    ... ({len(sorted_deduped) - show} more)")

    out = args.out or f"compaction_job_{md.get('Job ID','x')}.png"
    make_figure(md, inf, outf, out)


if __name__ == "__main__":
    main()
