# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Pesi (pesi3d.de)
"""Time Plan Import's reading and clean up on a PDF — no Qt, no IngeTrazo.

    python tools/bench.py PLAN.pdf [--gap MM] [--scale N] [--plain]

Per page: texts (names, scales), geometry, filters, «Merge duplicates»,
«Close gaps» (MM real millimetres at 1:N, default 5 mm at 1:100) and the
style record. ``--plain`` also runs the plain pypdf route (no
content_stream short cuts) and checks that both give the same.
"""
import argparse
import os
import sys
import time
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import plan_import_tool as pit                                 # noqa: E402
from plan_import_tool import cleanup, content_stream, pdf_vectors as pv  # noqa: E402

STAGES = ("texts", "geometry", "filters", "merge", "gaps", "style")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pdf")
    ap.add_argument("--gap", type=float, default=5.0)
    ap.add_argument("--scale", type=float, default=100.0)
    ap.add_argument("--plain", action="store_true")
    a = ap.parse_args()
    p = pit.default_params()
    gap = a.gap / 1000.0 / pit.metres_per_point(a.scale)
    t = time.perf_counter()
    reader = pv.open_reader(a.pdf)
    n = len(reader.pages)
    print(f"{os.path.basename(a.pdf)}: {n} page(s), opened in "
          f"{time.perf_counter() - t:.2f} s; fast paths: "
          f"{content_stream.usable()}")
    print(f"{'page':>4} {'paths':>9} {'segments':>9} " + " ".join(
        f"{s:>8}" for s in STAGES) + (f" {'plain':>8}" if a.plain else ""))
    total = dict.fromkeys(STAGES, 0.0)
    for i in range(n):
        tm = {}
        t = time.perf_counter()
        texts = pv.page_texts(a.pdf, i, reader)
        tm["texts"] = time.perf_counter() - t
        t = time.perf_counter()
        vec = pv.extract(a.pdf, i, reader=reader)
        tm["geometry"] = time.perf_counter() - t
        with pv.gc_paused():
            t = time.perf_counter()
            kept = cleanup.filter_paths(vec.paths, p)
            runs, _st = pit.collect_runs(vec, p, kept)
            tm["filters"] = time.perf_counter() - t
            t = time.perf_counter()
            runs, _merged = cleanup.dedupe_overlaps(runs)
            tm["merge"] = time.perf_counter() - t
            t = time.perf_counter()
            runs, _closed = cleanup.close_gaps(runs, gap)
            tm["gaps"] = time.perf_counter() - t
            t = time.perf_counter()
            pit.style_record(vec, p, None, kept)
            tm["style"] = time.perf_counter() - t
        segs = sum(len(r) - 1 for path in vec.paths for r, _c in path.runs)
        line = f"{i + 1:>4} {len(vec.paths):>9,} {segs:>9,} " + " ".join(
            f"{tm[s]:>7.2f}s" for s in STAGES)
        if a.plain:
            with mock.patch.object(content_stream, "usable",
                                   return_value=False):
                t = time.perf_counter()
                same = (pv.extract(a.pdf, i, reader=reader) == vec
                        and pv.page_texts(a.pdf, i, reader) == texts)
                line += f" {time.perf_counter() - t:>7.2f}s" + \
                    ("" if same else "  DIFFERENT!")
        print(line, flush=True)
        for s in STAGES:
            total[s] += tm[s]
    print(f"{'all':>4} {'':>9} {'':>9} " + " ".join(
        f"{total[s]:>7.2f}s" for s in STAGES))


if __name__ == "__main__":
    main()
