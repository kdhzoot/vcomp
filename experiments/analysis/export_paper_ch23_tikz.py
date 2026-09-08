#!/usr/bin/env python3
"""Export the exact native paper panels to standalone PDF and PNG assets."""
import argparse
from pathlib import Path
import shutil
import subprocess


STEMS = ('bg_loading_scale', 'bg_loading_flush_only', 'bg_loading_phase_breakdown',
         'bg_alternative_loading',
         'bg_alternative_lookup_work', 'bg_alternative_lookup_hits',
         'bg_alternative_read_throughput')


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--figure-dir', type=Path, required=True)
    p.add_argument('--build-dir', type=Path, required=True)
    args = p.parse_args()
    figures, build = args.figure_dir.resolve(), args.build_dir.resolve()
    build.mkdir(parents=True, exist_ok=False)
    for stem in STEMS:
        source = figures / (stem + '.tex')
        wrapper = build / (stem + '.tex')
        wrapper.write_text(
            '\\documentclass[10pt,border=2pt]{standalone}\n'
            '\\usepackage{times,tikz,amsmath}\n'
            '\\usetikzlibrary{patterns}\n'
            '\\begin{document}\n\\input{' + str(source) + '}\n'
            '\\end{document}\n')
        with (build / (stem + '.build.log')).open('w') as log:
            subprocess.run(['pdflatex', '-interaction=nonstopmode', '-halt-on-error', wrapper.name],
                           cwd=str(build), stdout=log, stderr=subprocess.STDOUT, check=True)
        subprocess.run(['gs', '-q', '-dSAFER', '-dBATCH', '-dNOPAUSE', '-sDEVICE=png16m',
                        '-r300', '-sOutputFile=' + str(build / (stem + '.png')),
                        str(build / (stem + '.pdf'))], check=True)
    # Install only after every panel exports successfully.
    for stem in STEMS:
        for suffix in ('.pdf', '.png'):
            shutil.copy2(build / (stem + suffix), figures / (stem + suffix))


if __name__ == '__main__':
    main()
