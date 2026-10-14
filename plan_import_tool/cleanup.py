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


def close_gaps(runs, gap):
    """Loose run ends (a point no other run shares) within ``gap`` points
    of another vertex are moved onto it; failing that, onto the nearest
    straight segment of another run, which is split there. Returns
    ``(runs, n_closed)``."""
    if gap <= 0 or not runs:
        return runs, 0
    runs = [(list(r), c) for r, c in runs]
    q = 1e-3

    def key(pt):
        return (round(pt[0] / q), round(pt[1] / q))

    count = {}
    for r, _c in runs:
        for pt in r:
            count[key(pt)] = count.get(key(pt), 0) + 1
    cell = gap
    grid = {}
    for ri, (r, _c) in enumerate(runs):
        for pi, pt in enumerate(r):
            grid.setdefault((int(pt[0] // cell), int(pt[1] // cell)),
                            []).append((ri, pi))
    # segments in coarser cells (a long line spans few of them); the cells
    # are at least as big as the gap, so the 3 × 3 around an end suffice
    scell = max(gap, 12.0)
    seg_grid = {}
    for ri, (r, _c) in enumerate(runs):
        for si, (a, b) in enumerate(zip(r, r[1:])):
            x0, x1 = sorted((a[0], b[0]))
            y0, y1 = sorted((a[1], b[1]))
            for gx in range(int(x0 // scell), int(x1 // scell) + 1):
                for gy in range(int(y0 // scell), int(y1 // scell) + 1):
                    seg_grid.setdefault((gx, gy), []).append((ri, si))
    closed = 0
    splits = {}                     # ri -> [(si, t, point)]
    for ri, (r, _c) in enumerate(runs):
        if len(r) < 2:
            continue
        for end in (0, len(r) - 1):
            pt = r[end]
            if count.get(key(pt), 0) > 1:
                continue                         # already connected
            gx, gy = int(pt[0] // cell), int(pt[1] // cell)
            best, bd = None, gap
            for ax in (gx - 1, gx, gx + 1):
                for ay in (gy - 1, gy, gy + 1):
                    for rj, pj in grid.get((ax, ay), ()):
                        if rj == ri and pj == end:
                            continue
                        if rj == ri and abs(pj - end) <= 1 and len(r) <= 2:
                            continue             # its own other end
                        qpt = runs[rj][0][pj]
                        d = math.hypot(qpt[0] - pt[0], qpt[1] - pt[1])
                        if 1e-9 < d < bd:
                            best, bd = qpt, d
            if best is not None:
                r[end] = best
                count[key(best)] = count.get(key(best), 0) + 1
                closed += 1
                continue
            # onto the nearest segment of another run
            bseg, bd = None, gap
            sx, sy = int(pt[0] // scell), int(pt[1] // scell)
            near = set()
            for ax in (sx - 1, sx, sx + 1):
                for ay in (sy - 1, sy, sy + 1):
                    near.update(seg_grid.get((ax, ay), ()))
            for rj, sj in near:
                if rj == ri:
                    continue
                a, b = runs[rj][0][sj], runs[rj][0][sj + 1]
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
            if bseg is not None:
                rj, sj, t, fp = bseg
                r[end] = fp
                splits.setdefault(rj, []).append((sj, t, fp))
                count[key(fp)] = count.get(key(fp), 0) + 2
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
