# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Pesi (pesi3d.de)
"""Filters and clean up for the imported linework — no Qt.

Filters (per PDF path, before anything is built):
- ``small``: drop paths smaller than N mm on paper (glyphs of text turned
  to curves, ticks, dots);
- ``colours``: drop paths of chosen colours;
- ``thin``: drop stroked lines thinner than N mm;
- ``hatches``: drop hatch lines — single straight lines of one colour and
  width lying in regular parallel rows (equal spacing, overlapping), as CAD
  programs print a hatch line by line.

Clean up (on the runs, in sheet points):
- ``dedupe_overlaps``: collinear segments lying on each other are merged
  (a wall outline drawn as fill AND stroke, double lines); every original
  end point stays a vertex, so junctions keep their vertices;
- ``close_gaps``: loose line ends within a tolerance are moved onto the
  nearest vertex — or onto the nearest line, which then gets a vertex
  there — so outlines close and walls meet.
"""
from __future__ import annotations

import math
from collections import Counter

PT_TO_MM = 25.4 / 72.0


def colour_key(rgb) -> str:
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, int(round(c * 255)))) for c in rgb)


def path_colour(path, p) -> str | None:
    """The colour that makes ``path`` count (stroke first, like
    collect_runs), or None when it is not used at all."""
    if path.stroke and p.get("strokes", True):
        return colour_key(path.stroke_rgb)
    if path.fill and p.get("fills", True):
        return colour_key(path.fill_rgb)
    return None


def _bbox(runs):
    xs = [x for r, _c in runs for x, _y in r]
    ys = [y for r, _c in runs for _x, y in r]
    return min(xs), min(ys), max(xs), max(ys)


def _single_line(path):
    """(a, b) when the path is ONE straight segment, else None."""
    if len(path.runs) != 1:
        return None
    r, _c = path.runs[0]
    if len(r) != 2:
        return None
    return r[0], r[1]


def hatch_paths(paths, max_gap_mm=2.2, min_lines=5):
    """Indices of the paths that are hatch lines: straight single lines of
    one colour and width, in rows of ≥ ``min_lines`` parallels with equal
    spacing (≤ ``max_gap_mm`` on paper, ±25 %), each overlapping its
    neighbours along the line."""
    gap = max_gap_mm / PT_TO_MM
    groups = {}
    for i, path in enumerate(paths):
        ln = _single_line(path)
        if ln is None:
            continue
        (ax, ay), (bx, by) = ln
        dx, dy = bx - ax, by - ay
        L = math.hypot(dx, dy)
        if L < 0.3 / PT_TO_MM:
            continue
        ang = math.degrees(math.atan2(dy, dx)) % 180.0
        key = (colour_key(path.stroke_rgb if path.stroke else path.fill_rgb),
               round(path.width, 2), round(ang * 2) / 2 % 180)
        groups.setdefault(key, []).append((i, ax, ay, bx, by))
    out = set()
    for key, segs in groups.items():
        if len(segs) < min_lines:
            continue
        ang = math.radians(key[2])
        ux, uy = math.cos(ang), math.sin(ang)
        nx, ny = -uy, ux
        rows = []
        for i, ax, ay, bx, by in segs:
            d = ax * nx + ay * ny
            s0 = ax * ux + ay * uy
            s1 = bx * ux + by * uy
            if s0 > s1:
                s0, s1 = s1, s0
            rows.append((d, s0, s1, i))
        rows.sort()
        # walk the lines in order of their offset: chains of neighbours
        # with overlapping extents and equal spacing
        n = len(rows)
        used = [False] * n
        for k in range(n):
            if used[k]:
                continue
            chain = [k]
            spacing = None
            j = k
            while True:
                d, s0, s1, _i = rows[j]
                best = None
                for m in range(j + 1, n):
                    dm, t0, t1, _im = rows[m]
                    step = dm - d
                    if step > gap:
                        break
                    if step < 0.05 / PT_TO_MM or used[m]:
                        continue
                    if min(s1, t1) - max(s0, t0) <= 0:      # no overlap
                        continue
                    if spacing is not None and \
                            abs(step - spacing) > 0.25 * spacing:
                        continue
                    best = m
                    break
                if best is None:
                    break
                if spacing is None:
                    spacing = rows[best][0] - d
                chain.append(best)
                j = best
            if len(chain) >= min_lines:
                for c in chain:
                    used[c] = True
                    out.add(rows[c][3])
    return out


def filter_paths(paths, p):
    """The paths kept by the filter options in ``p``: ``min_part_mm``,
    ``skip_colours`` (list of #rrggbb), ``min_width_mm``, ``skip_hatch``.
    Returns ``(kept_paths, counts)``."""
    small = float(p.get("min_part_mm", 0.0) or 0.0) / PT_TO_MM
    skip = set(p.get("skip_colours") or ())
    thin = float(p.get("min_width_mm", 0.0) or 0.0) / PT_TO_MM
    hatch = hatch_paths(paths) if p.get("skip_hatch") else set()
    kept = []
    counts = {"small": 0, "colour": 0, "thin": 0, "hatch": 0}
    for i, path in enumerate(paths):
        if i in hatch:
            counts["hatch"] += 1
            continue
        col = path_colour(path, p)
        if col is not None and col in skip:
            counts["colour"] += 1
            continue
        if thin > 0 and path.stroke and not path.fill and path.width < thin:
            counts["thin"] += 1
            continue
        if small > 0 and path.runs:
            x0, y0, x1, y1 = _bbox(path.runs)
            if max(x1 - x0, y1 - y0) < small:
                counts["small"] += 1
                continue
        kept.append(path)
    return kept, counts


def colour_table(paths, p):
    """{#rrggbb: number of paths} of the paths the content options use."""
    out = {}
    for path in paths:
        c = path_colour(path, p)
        if c is not None:
            out[c] = out.get(c, 0) + 1
    return out


# ---------------------------------------------------------------- clean up

def dedupe_overlaps(runs, tol=0.05):
    """Straight segments lying on each other (collinear, overlapping) are
    merged; all their end points stay vertices. Curved runs are kept as
    they are. ``tol`` in points."""
    curved, segs = [], []
    for run, cv in runs:
        if cv and len(run) > 2:
            curved.append((run, cv))
            continue
        for a, b in zip(run, run[1:]):
            if a != b:
                segs.append((a, b))
    lines = {}
    for a, b in segs:
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy)
        if L <= 1e-9:
            continue
        ang = math.atan2(dy, dx) % math.pi
        if ang > math.pi - 1e-4:
            ang = 0.0
        ux, uy = math.cos(ang), math.sin(ang)
        d = -a[0] * uy + a[1] * ux
        key = (round(ang * 2000), round(d / tol))
        s0 = a[0] * ux + a[1] * uy
        s1 = b[0] * ux + b[1] * uy
        lines.setdefault(key, []).append((min(s0, s1), max(s0, s1), a, b))
    out = list(curved)
    merged = 0
    for key, items in lines.items():
        if len(items) == 1:
            _s0, _s1, a, b = items[0]
            out.append(([a, b], False))
            continue
        items.sort()
        # clusters of overlapping intervals
        cl = [[items[0]]]
        hi = items[0][1]
        for it in items[1:]:
            if it[0] < hi - tol:
                cl[-1].append(it)
                hi = max(hi, it[1])
            else:
                cl.append([it])
                hi = it[1]
        for c in cl:
            if len(c) == 1:
                out.append(([c[0][2], c[0][3]], False))
                continue
            merged += len(c) - 1
            # all end points of the cluster, in order along the line
            pts = {}
            for s0, s1, a, b in c:
                for q, s in ((a, None), (b, None)):
                    ang = key[0] / 2000.0
                    s = q[0] * math.cos(ang) + q[1] * math.sin(ang)
                    pts[round(s / tol)] = q
            order = [pts[k] for k in sorted(pts)]
            # keep only pieces covered by some segment
            ang = key[0] / 2000.0
            ux, uy = math.cos(ang), math.sin(ang)
            ivs = [(s0, s1) for s0, s1, _a, _b in c]
            for p0, p1 in zip(order, order[1:]):
                m = ((p0[0] + p1[0]) * 0.5) * ux + ((p0[1] + p1[1]) * 0.5) * uy
                if any(s0 - tol <= m <= s1 + tol for s0, s1 in ivs):
                    out.append(([p0, p1], False))
    return out, merged


def _ring(ix, iy, k):
    """The grid cells at Chebyshev distance ``k`` from cell (ix, iy)."""
    xs = range(ix - k, ix + k + 1)
    ys = range(iy - k + 1, iy + k)
    return ([(x, iy - k) for x in xs] + [(x, iy + k) for x in xs]
            + [(ix - k, y) for y in ys] + [(ix + k, y) for y in ys])


class _GapSearch:
    """The nearest vertex or segment to a loose run end, for close_gaps.

    Up to 1.2.1 the search looked at the 3 × 3 cells round the end, cells of
    ``gap`` for vertices and of 12 pt for segments — on a dense sheet some
    thousands of segments for every end (40 s on an A0 Revit plan). Here
    vertices lie in cells of at most 1 pt and segments in 2 pt cells along
    their length; the cells are searched nearest ring first, until nothing
    nearer can come.

    The answers stay exactly those of the old search. It saw every point
    where it lay at the start (``moved`` keeps that for the ends moved
    since) and, of equal distances, kept the first it met: in its cell
    order for vertices, in the order of a set for segments — on such a tie
    the old segment search is asked itself (``_old_segment``)."""

    SCELL = 2.0           # segment cells, points
    OLD_SCELL = 12.0      # the old segment cells (at least)

    def __init__(self, runs, gap):
        self.runs = runs
        self.gap = gap
        self.vcell = min(gap, 1.0)
        self.moved = {}                # (ri, end) -> where that end lay first
        self.vgrid = {}
        self.sgrid = {}
        self.old = None                # the old segment grid, built when needed
        self.oldcell = max(gap, self.OLD_SCELL)
        # The old segment search reached at least ``oldcell`` round the end;
        # a segment within ``gap`` whose ends moved by less than ``gap`` lay
        # within 2 × gap at the start — inside, unless the gap is large.
        self.check_window = 2.0 * gap > self.oldcell
        vc = self.vcell
        vgrid = self.vgrid
        get = vgrid.get
        register = self._register
        for ri, (r, _c) in enumerate(runs):
            px = py = 0.0
            for pi, (x, y) in enumerate(r):
                k = (int(x // vc), int(y // vc))
                lst = get(k)
                if lst is None:
                    vgrid[k] = [(ri, pi)]
                else:
                    lst.append((ri, pi))
                if pi:
                    register((ri, pi - 1), px, py, x, y)
                px, py = x, y

    def _register(self, item, ax, ay, bx, by):
        """Segment ``item`` into every segment cell it passes through."""
        sc = self.SCELL
        eps = 1e-7
        lx, hx = (ax, bx) if ax <= bx else (bx, ax)
        ly, hy = (ay, by) if ay <= by else (by, ay)
        gx0, gx1 = int((lx - eps) // sc), int((hx + eps) // sc)
        gy0, gy1 = int((ly - eps) // sc), int((hy + eps) // sc)
        nx, ny = gx1 - gx0, gy1 - gy0
        if nx == 0 or ny == 0 or (nx + 1) * (ny + 1) <= 6:
            cells = [(gx, gy) for gx in range(gx0, gx1 + 1)
                     for gy in range(gy0, gy1 + 1)]
        elif nx >= ny:                    # column by column
            if ax > bx:
                ax, ay, bx, by = bx, by, ax, ay
            s = (by - ay) / (bx - ax)
            cells = []
            for gx in range(gx0, gx1 + 1):
                xl = max(gx * sc, ax)
                xr = max(xl, min(gx * sc + sc, bx))
                yl = ay + (xl - ax) * s
                yr = ay + (xr - ax) * s
                if yl > yr:
                    yl, yr = yr, yl
                cells += [(gx, gy) for gy in range(int((yl - eps) // sc),
                                                   int((yr + eps) // sc) + 1)]
        else:                             # row by row
            if ay > by:
                ax, ay, bx, by = bx, by, ax, ay
            s = (bx - ax) / (by - ay)
            cells = []
            for gy in range(gy0, gy1 + 1):
                yl = max(gy * sc, ay)
                yr = max(yl, min(gy * sc + sc, by))
                xl = ax + (yl - ay) * s
                xr = ax + (yr - ay) * s
                if xl > xr:
                    xl, xr = xr, xl
                cells += [(gx, gy) for gx in range(int((xl - eps) // sc),
                                                   int((xr + eps) // sc) + 1)]
        grid = self.sgrid
        get = grid.get
        for c in cells:
            lst = get(c)
            if lst is None:
                grid[c] = [item]
            else:
                lst.append(item)

    def move(self, ri, end, pt):
        """Run ``ri``'s end ``end`` (an index: 0 or the last) onto ``pt``."""
        r = self.runs[ri][0]
        self.moved.setdefault((ri, end), r[end])
        r[end] = pt
        vc = self.vcell
        k = (int(pt[0] // vc), int(pt[1] // vc))
        lst = self.vgrid.get(k)
        if lst is None:
            self.vgrid[k] = [(ri, end)]
        else:
            lst.append((ri, end))
        si = 0 if end == 0 else len(r) - 2
        a, b = r[si], r[si + 1]
        self._register((ri, si), a[0], a[1], b[0], b[1])

    def nearest_vertex(self, ri, end, pt):
        """The vertex the old search moved this end onto, or None."""
        gap, vc = self.gap, self.vcell
        px, py = pt
        runs, moved, get = self.runs, self.moved, self.vgrid.get
        n = len(runs[ri][0])
        ox, oy = int(px // gap) - 1, int(py // gap) - 1   # the old 3 × 3 block
        ix, iy = int(px // vc), int(py // vc)
        hypot = math.hypot
        best = bkey = None
        bd = gap
        k = 0
        while True:
            if k:
                lb = min(px - (ix - k + 1) * vc, (ix + k) * vc - px,
                         py - (iy - k + 1) * vc, (iy + k) * vc - py) - 1e-9
                if lb >= gap or (best is not None and lb > bd):
                    break
                cells = _ring(ix, iy, k)
            else:
                cells = ((ix, iy),)
            for c in cells:
                for rj, pj in get(c, ()):
                    if rj == ri and (pj == end
                                     or (n <= 2 and abs(pj - end) <= 1)):
                        continue                # itself, its own other end
                    q = runs[rj][0][pj]
                    d = hypot(q[0] - px, q[1] - py)
                    if not 1e-9 < d <= bd or (d == bd and best is None):
                        continue
                    o = moved.get((rj, pj), q)  # where the old search saw it
                    cx, cy = int(o[0] // gap) - ox, int(o[1] // gap) - oy
                    if not (0 <= cx <= 2 and 0 <= cy <= 2):
                        continue
                    key = (cx * 3 + cy, rj, pj)   # the order it met them in
                    if d < bd or key < bkey:
                        best, bd, bkey = q, d, key
            k += 1
        return best

    def nearest_segment(self, ri, pt):
        """``(rj, sj, t, foot)``: the straight segment of another run the old
        search moved this end onto, or None."""
        gap, sc = self.gap, self.SCELL
        px, py = pt
        runs, get = self.runs, self.sgrid.get
        hypot = math.hypot
        best = None
        bd = gap
        tie = False
        ix, iy = int(px // sc), int(py // sc)
        if gap <= sc:                 # the cells under the box round the end
            g = gap + 1e-7
            box = [(cx, cy)
                   for cx in range(int((px - g) // sc), int((px + g) // sc) + 1)
                   for cy in range(int((py - g) // sc), int((py + g) // sc) + 1)]
        else:
            box = None
        k = 0
        while True:
            if box is not None:
                if k:
                    break
                cells = box
            elif k:
                lb = min(px - (ix - k + 1) * sc, (ix + k) * sc - px,
                         py - (iy - k + 1) * sc, (iy + k) * sc - py) - 1e-9
                if lb >= gap or (best is not None and lb > bd):
                    break
                cells = _ring(ix, iy, k)
            else:
                cells = ((ix, iy),)
            for c in cells:
                for rj, sj in get(c, ()):
                    if rj == ri:
                        continue
                    r = runs[rj][0]
                    a, b = r[sj], r[sj + 1]
                    dx, dy = b[0] - a[0], b[1] - a[1]
                    L2 = dx * dx + dy * dy
                    if L2 <= 1e-12:
                        continue
                    t = ((px - a[0]) * dx + (py - a[1]) * dy) / L2
                    if t <= 0.0 or t >= 1.0:
                        continue
                    fx, fy = a[0] + t * dx, a[1] + t * dy
                    d = hypot(fx - px, fy - py)
                    if 1e-9 < d < bd:
                        best, bd, tie = (rj, sj, t, (fx, fy)), d, False
                    elif d == bd and best is not None and \
                            (rj != best[0] or sj != best[1]):
                        tie = True
            k += 1
        if best is not None and (tie or (self.check_window
                                         and not self._in_old_window(best, pt))):
            return self._old_segment(ri, pt)
        return best

    def _first(self, rj, pj):
        """Where vertex ``pj`` of run ``rj`` lay at the start."""
        return self.moved.get((rj, pj), self.runs[rj][0][pj])

    def _in_old_window(self, seg, pt):
        """Did the old search see segment ``seg`` from ``pt``?"""
        sc = self.oldcell
        a, b = self._first(seg[0], seg[1]), self._first(seg[0], seg[1] + 1)
        sx, sy = int(pt[0] // sc), int(pt[1] // sc)
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        return (int(x0 // sc) <= sx + 1 and int(x1 // sc) >= sx - 1
                and int(y0 // sc) <= sy + 1 and int(y1 // sc) >= sy - 1)

    def _old_segment(self, ri, pt):
        """The segment search of 1.2.1, unchanged — for ties and far moves."""
        sc = self.oldcell
        if self.old is None:
            grid = self.old = {}
            for rj, (r, _c) in enumerate(self.runs):
                for sj in range(len(r) - 1):
                    a, b = self._first(rj, sj), self._first(rj, sj + 1)
                    x0, x1 = sorted((a[0], b[0]))
                    y0, y1 = sorted((a[1], b[1]))
                    for gx in range(int(x0 // sc), int(x1 // sc) + 1):
                        for gy in range(int(y0 // sc), int(y1 // sc) + 1):
                            grid.setdefault((gx, gy), []).append((rj, sj))
        bseg, bd = None, self.gap
        sx, sy = int(pt[0] // sc), int(pt[1] // sc)
        near = set()
        for ax in (sx - 1, sx, sx + 1):
            for ay in (sy - 1, sy, sy + 1):
                near.update(self.old.get((ax, ay), ()))
        for rj, sj in near:
            if rj == ri:
                continue
            a, b = self.runs[rj][0][sj], self.runs[rj][0][sj + 1]
            dx, dy = b[0] - a[0], b[1] - a[1]
            L2 = dx * dx + dy * dy
            if L2 <= 1e-12:
                continue
            t = ((pt[0] - a[0]) * dx + (pt[1] - a[1]) * dy) / L2
            if t <= 0.0 or t >= 1.0:
                continue
            fx, fy = a[0] + t * dx, a[1] + t * dy
            d = math.hypot(fx - pt[0], fy - pt[1])
            if 1e-9 < d < bd:
                bseg, bd = (rj, sj, t, (fx, fy)), d
        return bseg


def close_gaps(runs, gap):
    """Loose run ends (a point no other run shares) within ``gap`` points
    of another vertex are moved onto it; failing that, onto the nearest
    straight segment of another run, which is split there. Returns
    ``(runs, n_closed)``."""
    if gap <= 0 or not runs:
        return runs, 0
    runs = [(list(r), c) for r, c in runs]
    q = 1e-3
    count = Counter((round(x / q), round(y / q))
                    for r, _c in runs for x, y in r)
    search = _GapSearch(runs, gap)
    closed = 0
    splits = {}                     # ri -> [(si, t, point)]
    for ri, (r, _c) in enumerate(runs):
        if len(r) < 2:
            continue
        for end in (0, len(r) - 1):
            pt = r[end]
            if count.get((round(pt[0] / q), round(pt[1] / q)), 0) > 1:
                continue                         # already connected
            best = search.nearest_vertex(ri, end, pt)
            if best is not None:
                search.move(ri, end, best)
                k = (round(best[0] / q), round(best[1] / q))
                count[k] = count.get(k, 0) + 1
                closed += 1
                continue
            # onto the nearest segment of another run
            bseg = search.nearest_segment(ri, pt)
            if bseg is not None:
                rj, sj, t, fp = bseg
                search.move(ri, end, fp)
                splits.setdefault(rj, []).append((sj, t, fp))
                k = (round(fp[0] / q), round(fp[1] / q))
                count[k] = count.get(k, 0) + 2
                closed += 1
    for rj, lst in splits.items():
        r, c = runs[rj]
        new = []
        by = {}
        for sj, t, fp in lst:
            by.setdefault(sj, []).append((t, fp))
        for si in range(len(r) - 1):
            new.append(r[si])
            for _t, fp in sorted(by.get(si, ())):
                new.append(fp)
        new.append(r[-1])
        runs[rj] = (new, c)
    return [(r, c) for r, c in runs], closed
