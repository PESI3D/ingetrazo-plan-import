# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Pesi (pesi3d.de)
"""Crop frames suggested from the sheet itself — no Qt.

The idea: the drawings of a sheet are the big clusters of lines; sheet
frame, separator lines, title block, legends, north arrow and fold marks
are not. So

1. the sheet frame is found (long axis-parallel lines near the paper
   edge) and dropped, together with separator lines running from frame
   side to frame side;
2. every remaining line is rasterised on a coarse grid (``CELL`` mm),
   which is grown by ``GAP`` mm so that a drawing with its dimension
   chains reads as one cluster;
3. clusters are labelled; the title block (title-block words on it, or a
   narrow cluster leaning on the frame), and everything much smaller
   than the biggest cluster are dropped;
4. the frames of what is left, plus a margin, are the suggestions — one
   per drawing (two sections on a sheet → two frames). Level markers
   left/right of an elevation (±0,00 …) are taken into its frame.
"""
from __future__ import annotations

import re

PT_TO_MM = 25.4 / 72.0
CELL = 2.0          # grid cell, mm on paper
GAP = 5.0           # lines closer than this belong to one drawing, mm
MARGIN = 3.0        # around a suggested frame, mm
MIN_DENSITY = 0.04  # share of grid cells with lines inside a drawing
KEEP = 0.15         # clusters below this × the biggest one's area: dropped

_TITLE_WORDS = re.compile(
    r"bauherr|bauvorhaben|bauherrschaft|projekt|plan\s*-?\s*nr|plannummer|"
    r"planinhalt|plan\s*inhalt|gezeichnet|geprüft|gepr\.|datum|index|"
    r"architekt|entwurfsverfasser|maßstab|massstab|format|blatt|"
    r"änderung|aenderung|client|project|drawing\s*no|drawn|checked|"
    r"revision|date|sheet", re.I)
_LEVEL = re.compile(r"^(±|[+\-−])?\s?\d{1,3}[,.]\d{2}")


def _segments(runs):
    import numpy as np
    a, b = [], []
    for pts, _c in runs:
        if len(pts) < 2:
            continue
        p = np.asarray(pts, dtype=float)
        a.append(p[:-1])
        b.append(p[1:])
    if not a:
        return np.zeros((0, 2)), np.zeros((0, 2))
    return np.concatenate(a), np.concatenate(b)


def find_frame(w, h, a, b):
    """The sheet frame ``(x0, y0, x1, y1)`` in points, or None, plus a mask
    of the frame/separator segments."""
    import numpy as np
    mm = 1.0 / PT_TO_MM
    d = b - a
    ln = np.hypot(d[:, 0], d[:, 1])
    hor = (np.abs(d[:, 1]) < 0.5) & (np.abs(d[:, 0]) > 0.4 * w)
    ver = (np.abs(d[:, 0]) < 0.5) & (np.abs(d[:, 1]) > 0.4 * h)
    near = 40 * mm
    ys = a[:, 1]
    xs = a[:, 0]
    bottom = hor & (ys < near)
    top = hor & (ys > h - near)
    left = ver & (xs < near)
    right = ver & (xs > w - near)
    if not (bottom.any() and top.any() and left.any() and right.any()):
        return None, np.zeros(len(a), bool)
    # the innermost line of each side
    x0 = xs[left].max()
    x1 = xs[right].min()
    y0 = ys[bottom].max()
    y1 = ys[top].min()
    frame = (x0, y0, x1, y1)
    tol = 3 * mm
    lo = np.minimum(a, b)
    hi = np.maximum(a, b)
    # frame lines and separators running from frame side to frame side
    sep_h = hor & (lo[:, 0] <= x0 + tol) & (hi[:, 0] >= x1 - tol)
    sep_v = ver & (lo[:, 1] <= y0 + tol) & (hi[:, 1] >= y1 - tol)
    out = (lo[:, 0] < x0 - tol) | (hi[:, 0] > x1 + tol) | \
          (lo[:, 1] < y0 - tol) | (hi[:, 1] > y1 + tol)      # outside
    drop = sep_h | sep_v | out | bottom | top | left | right
    del ln
    return frame, drop


def title_block(frame, texts, a, b):
    """The title block ``(x0, y0, x1, y1)``: the words of a title block
    (Bauherr, Projekt, Datum, Maßstab …) found together, grown to the
    lines around them — its left border running down to the frame, its
    top border. None without such words."""
    import numpy as np
    mm = 1.0 / PT_TO_MM
    hits = [t for t in texts if _TITLE_WORDS.search(t.text or "")]
    if len(hits) < 2:
        return None
    # the biggest group of words close together (title blocks are compact)
    best = []
    for t in hits:
        grp = [u for u in hits if abs(u.x - t.x) < 120 * mm
               and abs(u.y - t.y) < 200 * mm]
        if len(grp) > len(best):
            best = grp
    if len(best) < 2:
        return None
    tx0 = min(t.x for t in best)
    ty0 = min(t.y for t in best)
    ty1 = max(t.y + t.size for t in best)
    tx1 = max(t.x for t in best)
    fx0, fy0, fx1, fy1 = frame
    x0 = tx0 - 3 * mm
    y1 = ty1 + 5 * mm
    d = b - a
    lo = np.minimum(a, b)
    hi = np.maximum(a, b)
    ver = np.abs(d[:, 0]) < 0.5
    hor = np.abs(d[:, 1]) < 0.5
    # left border: a vertical line just left of the words, reaching the
    # frame bottom (or the frame top) and passing the words
    cand = ver & (a[:, 0] < tx0) & (a[:, 0] > tx0 - 25 * mm) & \
        (lo[:, 1] < ty0) & (hi[:, 1] > ty1 - 2 * mm)
    bottom = (lo[:, 1] - fy0) < 5 * mm
    topside = (fy1 - hi[:, 1]) < 5 * mm
    c = cand & (bottom | topside)
    if c.any():
        k = np.argmin(np.where(c, tx0 - a[:, 0], np.inf))
        x0 = a[k, 0] - 0.5
        if bottom[k]:
            y0, y1 = fy0, max(y1, hi[k, 1] + 0.5)
        else:
            y0, y1 = min(ty0, lo[k, 1]) - 0.5, fy1
    else:
        y0 = fy0 if ty0 - fy0 < 60 * mm else ty0 - 5 * mm
    # a top border line across the block lifts it to there
    top = hor & (lo[:, 0] <= x0 + 3 * mm) & (hi[:, 0] >= fx1 - 3 * mm) & \
        (a[:, 1] >= ty1) & (a[:, 1] < y1 + 60 * mm)
    if top.any() and y0 == fy0:
        y1 = max(y1, float(a[top, 1].min()) + 0.5)
    x1 = fx1 if fx1 - tx1 < 80 * mm else tx1 + 40 * mm
    return (x0, y0, x1, y1)


def title_box(frame, a, b):
    """A title block drawn as a box in the frame's lower right corner: a
    vertical line standing on the frame bottom and a horizontal line
    running to the frame's right side, meeting in a corner. The biggest
    such box under a third of the frame. Works without any text (text
    as curves)."""
    import numpy as np
    mm = 1.0 / PT_TO_MM
    fx0, fy0, fx1, fy1 = frame
    fw, fh = fx1 - fx0, fy1 - fy0
    lo = np.minimum(a, b)
    hi = np.maximum(a, b)
    d = b - a
    tol = 6 * mm
    hs = np.nonzero((np.abs(d[:, 1]) < 0.5) & (fx1 - hi[:, 0] < tol)
                    & (a[:, 1] < fy0 + 0.6 * fh) & (a[:, 1] > fy0 + 5 * mm)
                    & (hi[:, 0] - lo[:, 0] > 20 * mm))[0]
    vs = np.nonzero((np.abs(d[:, 0]) < 0.5) & (lo[:, 1] - fy0 < tol)
                    & (a[:, 0] > fx0 + 0.4 * fw) & (a[:, 0] < fx1 - 20 * mm)
                    & (hi[:, 1] - lo[:, 1] > 15 * mm))[0]
    if not len(hs) or not len(vs):
        return None
    best = None
    c = 3 * mm
    for v in vs[:400]:
        vx, vtop = a[v, 0], hi[v, 1]
        ok = hs[(np.abs(lo[hs, 0] - vx) < c) & (np.abs(a[hs, 1] - vtop) < c)]
        if not len(ok):
            continue
        y = float(a[ok, 1].max())
        area = (fx1 - vx) * (y - fy0)
        if area > fw * fh / 3:
            continue
        if best is None or area > best[0]:
            best = (area, (vx - 0.5, fy0, fx1, y + 0.5))
    return best[1] if best else None


def _raster(w, h, a, b, cell):
    import numpy as np
    nx = int(w / cell) + 2
    ny = int(h / cell) + 2
    grid = np.zeros((ny, nx), bool)
    d = b - a
    n = np.maximum(1, np.ceil(np.hypot(d[:, 0], d[:, 1]) / (cell * 0.5))
                   ).astype(int)
    idx = np.repeat(np.arange(len(a)), n + 1)
    start = np.repeat(np.cumsum(n + 1) - (n + 1), n + 1)
    t = (np.arange(idx.size) - start) / np.repeat(n, n + 1)
    p = a[idx] + d[idx] * t[:, None]
    ix = np.clip((p[:, 0] / cell).astype(int), 0, nx - 1)
    iy = np.clip((p[:, 1] / cell).astype(int), 0, ny - 1)
    grid[iy, ix] = True
    return grid


def _grow(grid, r):
    import numpy as np
    g = grid.copy()
    for _ in range(r):
        s = g.copy()
        s[1:, :] |= g[:-1, :]
        s[:-1, :] |= g[1:, :]
        s[:, 1:] |= g[:, :-1]
        s[:, :-1] |= g[:, 1:]
        g = s
    return g


def _label(grid):
    """Connected clusters (4-neighbours): list of (ix0, iy0, ix1, iy1,
    cells)."""
    import numpy as np
    ny, nx = grid.shape
    seen = np.zeros_like(grid)
    out = []
    ys, xs = np.nonzero(grid)
    for y, x in zip(ys.tolist(), xs.tolist()):
        if seen[y, x]:
            continue
        seen[y, x] = True
        stack = [(y, x)]
        x0 = x1 = x
        y0 = y1 = y
        n = 0
        while stack:
            cy, cx = stack.pop()
            n += 1
            if cx < x0: x0 = cx
            if cx > x1: x1 = cx
            if cy < y0: y0 = cy
            if cy > y1: y1 = cy
            for yy, xx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1),
                           (cy, cx - 1)):
                if 0 <= yy < ny and 0 <= xx < nx and grid[yy, xx] \
                        and not seen[yy, xx]:
                    seen[yy, xx] = True
                    stack.append((yy, xx))
        out.append((x0, y0, x1, y1, n))
    return out


def suggest_crops(width, height, runs, texts=()):
    """Suggested crop frames ``[(x0, y0, x1, y1)]`` in page points, top to
    bottom, left to right. ``texts``: PageText-like objects (x, y, size,
    text). Empty when nothing sensible is found."""
    import numpy as np
    mm = 1.0 / PT_TO_MM
    a, b = _segments(runs)
    if not len(a):
        return []
    frame, drop = find_frame(width, height, a, b)
    tb = title_box(frame, a, b) if frame is not None else None
    a, b = a[~drop], b[~drop]
    if not len(a):
        return []
    texts = list(texts or ())
    fx0, fy0, fx1, fy1 = frame if frame else (0, 0, width, height)
    if tb is None:
        tb = title_block((fx0, fy0, fx1, fy1), texts, a, b)
    if tb is not None:          # lines touching the title block: out
        import numpy as np
        lo = np.minimum(a, b)
        hi = np.maximum(a, b)
        inside = (hi[:, 0] >= tb[0]) & (lo[:, 0] <= tb[2]) & \
            (hi[:, 1] >= tb[1]) & (lo[:, 1] <= tb[3])
        a, b = a[~inside], b[~inside]
        if not len(a):
            return []
    cell = CELL * mm
    raw = _raster(width, height, a, b, cell)
    grid = _grow(raw, max(1, int(round(GAP / CELL / 2))))
    clusters = _label(grid)
    if not clusters:
        return []
    shrink = int(round(GAP / CELL / 2))

    def box(c):
        x0, y0, x1, y1, _n = c
        return ((x0 + shrink) * cell, (y0 + shrink) * cell,
                (x1 + 1 - shrink) * cell, (y1 + 1 - shrink) * cell)

    def area(bx):
        return max(0.0, bx[2] - bx[0]) * max(0.0, bx[3] - bx[1])

    def titleish(bx):
        words = set()
        for t in ([] if tb is not None else texts):
            if bx[0] <= t.x <= bx[2] and bx[1] <= t.y <= bx[3]:
                for m in _TITLE_WORDS.finditer(t.text or ""):
                    words.add(m.group(0).lower()[:5])
        if len(words) >= 2:
            return True
        # narrow block leaning on the frame's right or bottom side
        lean = 12 * mm
        on_r = fx1 - bx[2] < lean
        on_b = bx[1] - fy0 < lean
        w_ = bx[2] - bx[0]
        h_ = bx[3] - bx[1]
        small = area(bx) < 0.12 * (fx1 - fx0) * (fy1 - fy0)
        return small and (on_r and w_ < 0.3 * (fx1 - fx0)) and \
            (on_b or h_ > 2.5 * w_)

    def density(c):
        x0, y0, x1, y1, _n = c
        # inside the cluster's outer ring: a frame round a few words is
        # empty there, a drawing is not
        r = 2 + int(round(GAP / CELL / 2))
        sub = raw[y0 + r:y1 + 1 - r, x0 + r:x1 + 1 - r]
        if sub.size == 0:
            return 1.0
        return float(sub.sum()) / sub.size

    # an almost empty box (a frame around a few words) is no drawing
    dense = [c for c in clusters if density(c) >= MIN_DENSITY]
    boxes = [box(c) for c in (dense or clusters)]
    boxes = [bx for bx in boxes if not titleish(bx)]
    if not boxes:
        return []
    big = max(area(bx) for bx in boxes)
    keep = [bx for bx in boxes if area(bx) >= KEEP * big]

    # level markers: texts like ±0,00 / +2,89 / -3,68 beside a drawing
    out = []
    for bx in keep:
        x0, y0, x1, y1 = bx
        hgt = y1 - y0
        for t in texts:
            if not _LEVEL.match((t.text or "").strip()):
                continue
            if not (y0 - 0.05 * hgt <= t.y <= y1 + 0.05 * hgt):
                continue
            if x0 - 80 * mm <= t.x < x0 or x1 < t.x <= x1 + 80 * mm:
                if any(o is not bx and o[0] <= t.x <= o[2]
                       and o[1] <= t.y <= o[3] for o in keep):
                    continue
                x0 = min(x0, t.x - 2 * mm)
                x1 = max(x1, t.x + max(1.0, t.size) * 3)
        if tb is not None and x1 > tb[0] and x0 < tb[2] and \
                y1 > tb[1] and y0 < tb[3]:
            # the frame would take the title block in: cut it off on the
            # side that loses less drawing
            def lost(r):
                ix0, iy0 = int(r[0] / cell), int(r[1] / cell)
                ix1, iy1 = int(r[2] / cell) + 1, int(r[3] / cell) + 1
                return int(raw[max(0, iy0):max(0, iy1),
                               max(0, ix0):max(0, ix1)].sum())
            cut_x = (x0, y0, tb[0] - 1, y1)
            cut_y = (x0, tb[3] + 1, x1, y1) if tb[1] <= fy0 + 6 * mm \
                else (x0, y0, x1, tb[1] - 1)
            whole = lost((x0, y0, x1, y1))
            keep_x = lost(cut_x)
            keep_y = lost(cut_y)
            x0, y0, x1, y1 = cut_x if keep_x >= keep_y else cut_y
            del whole
        m = MARGIN * mm
        out.append((max(fx0, x0 - m), max(fy0, y0 - m),
                    min(fx1, x1 + m), min(fy1, y1 + m)))
    out.sort(key=lambda r: (-round(r[3] / (40 * mm)), r[0]))
    return [tuple(round(v, 2) for v in r) for r in out]
