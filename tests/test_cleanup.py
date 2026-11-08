# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Pesi (pesi3d.de)
"""close_gaps must give exactly what 1.2.1 gave — on random, adversarial and
real linework, for gaps from 1 to 500 mm (at 1:100)."""
import os
import random
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import plan_import_tool as pit                       # noqa: E402
from plan_import_tool import cleanup, pdf_vectors   # noqa: E402

import _reference                                   # noqa: E402

MM = 1.0 / 1000.0 / pit.metres_per_point(100)       # 1 mm real at 1:100
GAPS = (1 * MM, 5 * MM, 20 * MM, 100 * MM, 500 * MM, 2.0, 3.0, 7.0, 12.0,
        20.0, 30.0)
EXAMPLES = os.path.join(os.path.dirname(HERE), "examples")


def lattice_runs(rng, n, step, extent):
    """Runs on a lattice: shared points, overlaps, equal distances."""
    runs = []
    for _ in range(n):
        k = rng.choice((1, 2, 2, 2, 3, 4, 5))
        x, y = rng.randrange(-extent, extent), rng.randrange(-extent, extent)
        pts = [(x * step, y * step)]
        for _ in range(k - 1):
            x += rng.randrange(-3, 4)
            y += rng.randrange(-3, 4)
            pts.append((x * step, y * step))
        runs.append((pts, rng.random() < 0.1 and len(pts) > 2))
    return runs


def float_runs(rng, n, extent):
    runs = []
    for _ in range(n):
        k = rng.choice((2, 2, 2, 3, 4))
        x, y = rng.uniform(-extent, extent), rng.uniform(-extent, extent)
        pts = [(x, y)]
        for _ in range(k - 1):
            x += rng.uniform(-extent / 4, extent / 4)
            y += rng.uniform(-extent / 4, extent / 4)
            pts.append((x, y))
        runs.append((pts, False))
    return runs


class CloseGapsTest(unittest.TestCase):
    def same(self, runs, gap, what=""):
        want = _reference.close_gaps(runs, gap)
        got = cleanup.close_gaps(runs, gap)
        self.assertEqual(want[1], got[1], f"closed count {what} gap={gap}")
        self.assertEqual(want[0], got[0], f"runs {what} gap={gap}")

    def test_lattice(self):
        rng = random.Random(1)
        for case in range(120):
            step = rng.choice((0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 4.0))
            runs = lattice_runs(rng, rng.randrange(5, 150), step,
                                rng.choice((5, 12, 40)))
            for gap in GAPS:
                self.same(runs, gap, f"lattice case {case}")

    def test_floats(self):
        rng = random.Random(2)
        for case in range(60):
            runs = float_runs(rng, rng.randrange(5, 200),
                              rng.choice((2.0, 10.0, 60.0)))
            for gap in GAPS:
                self.same(runs, gap, f"float case {case}")

    def test_adversarial(self):
        cases = {
            "identical segments, end over their middle": [
                ([(0.0, 0.0), (10.0, 0.0)], False),
                ([(0.0, 0.0), (10.0, 0.0)], False),
                ([(5.0, 0.1), (5.0, 3.0)], False)],
            "end between two vertices": [
                ([(-1.0, 0.0), (-1.0, -5.0)], False),
                ([(1.0, 0.0), (1.0, -5.0)], False),
                ([(0.0, 0.0), (0.0, 5.0)], False)],
            "ends on cell borders": [
                ([(2.0, 2.0), (4.0, 2.0)], False),
                ([(4.0, 2.141732283464567), (4.0, 6.0)], False),
                ([(6.0, 4.0), (8.0, 4.0)], False),
                ([(8.0, 3.9), (8.0, -4.0)], False)],
            "long diagonals": [
                ([(-1000.0, -700.0), (1000.0, 700.0)], False),
                ([(-1000.0, 700.0), (1000.0, -700.0)], False),
                ([(0.05, 0.3), (3.0, 9.0)], False),
                ([(500.0, 350.4), (520.0, 400.0)], False)],
            "zero length and single points": [
                ([(1.0, 1.0), (1.0, 1.0)], False),
                ([(1.05, 1.0)], False),
                ([(1.1, 1.0), (5.0, 1.0)], False),
                ([(0.0, 0.0), (1.0, 1.0)], False)],
            "closed rings": [
                ([(0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0),
                  (0.0, 0.0)], False),
                ([(5.1, 2.5), (9.0, 2.5)], False),
                ([(2.5, 5.08), (2.5, 9.0)], False)],
            "negative coordinates": [
                ([(-3.3, -3.3), (-1.0, -3.3)], False),
                ([(-0.95, -3.25), (-0.95, 8.0)], False),
                ([(-12.0, -24.0), (-11.9, -24.1)], False)],
        }
        for what, runs in cases.items():
            for gap in GAPS:
                self.same(runs, gap, what)

    def test_dense(self):
        """Many ends closing in a crowd: moved ends seen where they lay."""
        rng = random.Random(3)
        for case in range(15):
            runs = lattice_runs(rng, 400, 0.1, 8) + float_runs(rng, 100, 1.5)
            rng.shuffle(runs)
            for gap in (5 * MM, 0.3, 1.0, 2.5, 14.0, 26.0):
                self.same(runs, gap, f"dense case {case}")

    def test_examples(self):
        p = pit.default_params()
        for name in sorted(os.listdir(EXAMPLES)):
            if not name.endswith(".pdf"):
                continue
            path = os.path.join(EXAMPLES, name)
            for i in range(len(pdf_vectors.page_info(path))):
                runs, _st = pit.collect_runs(pdf_vectors.extract(path, i), p)
                runs, _merged = cleanup.dedupe_overlaps(runs)
                for gap in GAPS:
                    self.same(runs, gap, f"{name} p{i + 1}")


try:
    import numpy
except ImportError:                    # IngeTrazo has it; a bare Python may not
    numpy = None


@unittest.skipIf(numpy is None, "needs numpy")
class EdgeArraysTest(unittest.TestCase):
    def test_like_the_loop(self):
        """build_group's edges as 1.2.1 listed them, run by run."""
        rng = random.Random(4)
        for _case in range(50):
            runs = lattice_runs(rng, rng.randrange(0, 60), 0.5, 20)
            runs += [([(0.0, 0.0)], True), ([], False)]
            rng.shuffle(runs)
            xy, ia, ib, curved = pit.edge_arrays(runs)
            want_xy, want_ia, want_ib, want_c = [], [], [], []
            for run, c in runs:
                if len(run) < 2:
                    continue
                base, e0 = len(want_xy), len(want_ia)
                want_xy += run
                for k in range(len(run) - 1):
                    want_ia.append(base + k)
                    want_ib.append(base + k + 1)
                if c and len(run) > 2:
                    want_c.append((e0, len(want_ia)))
            self.assertEqual([tuple(p) for p in xy.tolist()], want_xy)
            self.assertEqual(ia.tolist(), want_ia)
            self.assertEqual(ib.tolist(), want_ib)
            self.assertEqual(curved, want_c)


if __name__ == "__main__":
    unittest.main()
