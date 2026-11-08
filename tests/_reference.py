# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Pesi (pesi3d.de)
"""The 1.2.1 implementations the faster ones must match, kept as oracles."""
import math


def close_gaps(runs, gap):
    """cleanup.close_gaps of Plan Import 1.2.1, unchanged."""
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
    splits = {}
    for ri, (r, _c) in enumerate(runs):
        if len(r) < 2:
            continue
        for end in (0, len(r) - 1):
            pt = r[end]
            if count.get(key(pt), 0) > 1:
                continue
            gx, gy = int(pt[0] // cell), int(pt[1] // cell)
            best, bd = None, gap
            for ax in (gx - 1, gx, gx + 1):
                for ay in (gy - 1, gy, gy + 1):
                    for rj, pj in grid.get((ax, ay), ()):
                        if rj == ri and pj == end:
                            continue
                        if rj == ri and abs(pj - end) <= 1 and len(r) <= 2:
                            continue
                        qpt = runs[rj][0][pj]
                        d = math.hypot(qpt[0] - pt[0], qpt[1] - pt[1])
                        if 1e-9 < d < bd:
                            best, bd = qpt, d
            if best is not None:
                r[end] = best
                count[key(best)] = count.get(key(best), 0) + 1
                closed += 1
                continue
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
