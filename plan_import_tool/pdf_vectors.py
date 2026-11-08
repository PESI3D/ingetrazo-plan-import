# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Pesi (pesi3d.de)
"""Vector geometry out of one PDF page — no rendering, no Qt.

Walks the page's content stream (and Form XObjects inside it) with the
graphics state (CTM, colours, line width) and returns every painted path
as polyline *runs* in page points, origin at the lower-left of the visible
page (CropBox), page rotation applied. Bezier segments are flattened; a
run of consecutive curve segments is flagged ``curved`` so the importer
can give it one curve id (an arc selects as one contour, like a drawn one).

Text (BT…ET), images and shadings are counted, never converted. Clipping
paths that are not painted (``W n``) are dropped.

PDF parsing itself is pypdf's (BSD-3, vendored in ``_vendor``); the hot
loops read the decoded streams faster in ``content_stream``.
"""
from __future__ import annotations

import contextlib
import gc
import math
import os
import re
import sys
import threading
from dataclasses import dataclass, field

_VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_vendor")

_GC_LOCK = threading.Lock()
_GC_HOLD = [0, False]        # holders, GC enabled before the first


@contextlib.contextmanager
def gc_paused():
    """No cyclic GC while millions of tuples (points, runs, edges) are
    built: every collection would walk all of them again — about half the
    time of a big sheet. IngeTrazo's own importers do the same. Nests and
    works across threads: GC comes back when the last holder leaves."""
    with _GC_LOCK:
        if _GC_HOLD[0] == 0:
            _GC_HOLD[1] = gc.isenabled()
            gc.disable()
        _GC_HOLD[0] += 1
    try:
        yield
    finally:
        with _GC_LOCK:
            _GC_HOLD[0] -= 1
            if _GC_HOLD[0] == 0 and _GC_HOLD[1]:
                gc.enable()


def _pypdf():
    """Import pypdf: the vendored copy (if no pypdf is installed)."""
    try:
        import pypdf  # noqa: F401
    except ImportError:
        if _VENDOR not in sys.path:
            sys.path.append(_VENDOR)
        import pypdf  # noqa: F401
    # pypdf reports what it repairs on its own («incorrect startxref
    # pointer», «parsing for Object Streams») as warnings — on every page
    # of many CAD plots. They say nothing to the user and filled
    # IngeTrazo's log; real errors still come through.
    import logging
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    return pypdf


PT_TO_MM = 25.4 / 72.0


@dataclass
class PdfPath:
    runs: list            # [(points [(x, y), ...], curved: bool), ...]
    stroke: bool
    fill: bool
    stroke_rgb: tuple
    fill_rgb: tuple
    width: float          # stroke width in page points (after CTM)
    evenodd: bool = False  # fill rule f* / B* / b*  (else non-zero)


@dataclass
class PageVectors:
    width: float          # visible page size in points
    height: float
    paths: list = field(default_factory=list)
    counts: dict = field(default_factory=dict)   # skipped things


# ---- small matrix helpers (PDF: [a b c d e f], row vectors) ---------------
def _mul(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return (a * A + b * C, a * B + b * D,
            c * A + d * C, c * B + d * D,
            e * A + f * C + E, e * B + f * D + F)


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _scale_of(m):
    a, b, c, d, _e, _f = m
    return math.sqrt(abs(a * d - b * c)) or 1.0


def _bezier(p0, p1, p2, p3, tol):
    """Flatten a cubic (already in page space); returns points after p0.
    Wang's bound: n = sqrt(3·M / (4·tol)) segments keep the polyline within
    ``tol`` of the curve (M = largest second difference) — a 1 mm glyph
    takes one or two segments, a big arc as many as it needs."""
    m = max(math.hypot(p0[0] - 2 * p1[0] + p2[0], p0[1] - 2 * p1[1] + p2[1]),
            math.hypot(p1[0] - 2 * p2[0] + p3[0], p1[1] - 2 * p2[1] + p3[1]))
    n = int(min(64, max(1, math.ceil(math.sqrt(0.75 * m / max(tol, 1e-6))))))
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u**3 * p0[0] + 3 * u * u * t * p1[0]
                    + 3 * u * t * t * p2[0] + t**3 * p3[0],
                    u**3 * p0[1] + 3 * u * u * t * p1[1]
                    + 3 * u * t * t * p2[1] + t**3 * p3[1]))
    return out


def _gray(v):
    return (v, v, v)


def _cmyk(c, m, y, k):
    return ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))


def _color(ops):
    try:
        v = [float(o) for o in ops if not hasattr(o, "startswith")
             or not str(o).startswith("/")]
    except (TypeError, ValueError):
        return None
    if len(v) == 1:
        return _gray(v[0])
    if len(v) == 3:
        return tuple(v)
    if len(v) == 4:
        return _cmyk(*v)
    return None


class _State:
    __slots__ = ("ctm", "srgb", "frgb", "lw")

    def __init__(self, ctm):
        self.ctm = ctm
        self.srgb = (0.0, 0.0, 0.0)
        self.frgb = (0.0, 0.0, 0.0)
        self.lw = 1.0

    def copy(self):
        s = _State(self.ctm)
        s.srgb, s.frgb, s.lw = self.srgb, self.frgb, self.lw
        return s


class PdfImportError(Exception):
    """A PDF that cannot be read (encrypted, damaged, no such page)."""


def open_reader(path):
    return _reader(path)


def _reader(path):
    pypdf = _pypdf()
    try:
        reader = pypdf.PdfReader(str(path))
        if reader.is_encrypted:
            # Many CAD plots are "protected" with an empty user password
            # (printing allowed, editing not) — those open fine.
            if not reader.decrypt(""):
                raise PdfImportError("The PDF is password-protected.")
        return reader
    except PdfImportError:
        raise
    except Exception as exc:                               # noqa: BLE001
        raise PdfImportError(f"Cannot read the PDF: {exc}") from exc


def page_info(path) -> list:
    """``[(width_pt, height_pt), ...]`` per page, as displayed (rotation)."""
    out = []
    for page in _reader(path).pages:
        box = page.cropbox
        w, h = float(box.width), float(box.height)
        if int(page.get("/Rotate", 0) or 0) % 180 == 90:
            w, h = h, w
        out.append((w, h))
    return out


_SCALE_LINE = re.compile(r"^(.*?)[\s\t]*\bM?\s*1\s*:\s*(\d{1,5})\s*$")
_SCALE_ANY = re.compile(r"\b1\s*:\s*(\d{1,5})\b")

# Imperial plan scales: architectural «1/4" = 1'-0"», «1 1/2" = 1'-0"»,
# engineering «1" = 20'» → the same 1:N (1/4" = 1'-0" is 1:48).
_IN_MARK = r"(?:\"|”|″|''|in\.?)"
_FT_MARK = r"(?:'|’|′|ft\.?)"
_IMP_SCALE = re.compile(
    r"(\d{1,2}\s*[- ]\s*\d{1,2}\s*/\s*\d{1,2}|\d{1,2}\s*/\s*\d{1,3}|\d{1,2})"
    r"\s*" + _IN_MARK + r"\s*=\s*(\d{1,4})\s*" + _FT_MARK
    + r"(?:\s*-?\s*0\s*" + _IN_MARK + r"?)?", re.I)


def _inch_value(txt):
    """«1/4», «1 1/2», «1-1/2», «3» → inches."""
    txt = txt.strip()
    m = re.match(r"^(\d+)\s*[- ]\s*(\d+)\s*/\s*(\d+)$", txt)
    if m:
        return int(m.group(1)) + int(m.group(2)) / max(1, int(m.group(3)))
    m = re.match(r"^(\d+)\s*/\s*(\d+)$", txt)
    if m:
        return int(m.group(1)) / max(1, int(m.group(2)))
    return float(txt)


def imperial_scale(text):
    """1:N of the first imperial scale in ``text`` — or None."""
    for m in _IMP_SCALE.finditer(text or ""):
        try:
            inch = _inch_value(m.group(1))
            n = int(m.group(2)) * 12.0 / inch if inch > 0 else 0
        except (ValueError, ZeroDivisionError):
            continue
        if 1.0 <= n <= 20000:
            return int(round(n))
    return None


def _scale_in(text):
    """1:N of a metric «1:100» or an imperial «1/4" = 1'-0"» scale."""
    m = _SCALE_ANY.search(text or "")
    if m and int(m.group(1)) > 1:
        return int(m.group(1))
    return imperial_scale(text)


@dataclass
class PageText:
    x: float              # baseline start, page points (like PageVectors)
    y: float
    size: float           # text height in page points
    angle: float          # degrees, counter-clockwise
    text: str
    scale: bool = False   # reads like a plan scale «1:100»


def _title_from(text):
    name = scale = None
    for line in text.splitlines():
        line = line.strip()
        mt = _SCALE_LINE.match(line)
        if mt and len(mt.group(1).strip()) >= 3:
            name = " ".join(mt.group(1).split())
            scale = int(mt.group(2))
            break
        mi = _IMP_SCALE.search(line)
        if mi and imperial_scale(line):
            head = re.sub(r"(?i)\bscale\s*:?\s*$", "",
                          line[:mi.start()]).strip(" :-–")
            if len(head) >= 3:
                name = " ".join(head.split())
                scale = imperial_scale(line)
                break
    if scale is None:
        found = [int(v) for v in _SCALE_ANY.findall(text) if int(v) > 1]
        found += [n for n in (imperial_scale(ln) for ln in text.splitlines())
                  if n]
        if found:
            scale = max(set(found), key=found.count)
    return name, scale


def page_texts(path, page_index: int, reader=None) -> tuple:
    """``(name, scale_n, [PageText])`` — the drawing label «Erdgeschoss
    1:100» CAD layouts print, and every text with its position for the
    page view. Slow-ish (pypdf text extraction): run it off the main
    thread for a whole set."""
    reader = reader or _reader(path)
    try:
        page = reader.pages[page_index]
        base, _w, _h = _page_base(page)
    except Exception:                                      # noqa: BLE001
        return None, None, []
    items = []

    def visit(text, cm, tm, _font, size):
        t = (text or "").strip()
        if not t:
            return
        try:
            m = _mul(_mul(tuple(float(v) for v in tm),
                          tuple(float(v) for v in cm)), base)
        except Exception:                                  # noqa: BLE001
            return
        a, b = m[0], m[1]
        k = math.hypot(a, b) or 1.0
        items.append(PageText(m[4], m[5], abs(float(size or 0)) * k,
                              math.degrees(math.atan2(b, a)), t,
                              bool(_SCALE_ANY.search(t)
                                   or imperial_scale(t))))

    try:
        text = _extract_text(reader, page, visit, items) or ""
    except Exception:                                      # noqa: BLE001
        return None, None, items
    name, scale = _title_from(text)
    return name, scale, items


def _extract_text(reader, page, visit, items):
    """pypdf's ``page.extract_text(visitor_text=visit)`` — from the text
    operators alone when they can be cut out (a sheet's few hundred texts
    instead of its 30 MB of lines); else, or on any trouble there, from the
    whole stream. (A stream pypdf cannot parse at all now still gives the
    texts of its text objects.)"""
    from . import content_stream
    if content_stream.usable():
        try:
            return content_stream.extract_text(reader, page, visit)
        except Exception:                                  # noqa: BLE001
            items.clear()             # texts of the short cut, if any
    return page.extract_text(visitor_text=visit)


def page_title(path, page_index: int, reader=None) -> tuple:
    """``(name, scale_n)`` — see :func:`page_texts`."""
    name, scale, _items = page_texts(path, page_index, reader)
    return name, scale


def page_count(path) -> int:
    return len(page_info(path))


def _page_base(page):
    """``(matrix, width, height)``: the crop origin to (0,0), then the page
    rotation (clockwise as displayed), so the result looks like the page
    on screen."""
    box = page.cropbox
    x0, y0 = float(box.left), float(box.bottom)
    w, h = float(box.width), float(box.height)
    rot = int(page.get("/Rotate", 0) or 0) % 360
    base = (1, 0, 0, 1, -x0, -y0)
    if rot == 90:
        base = _mul(base, (0, -1, 1, 0, 0, w))
        w, h = h, w
    elif rot == 180:
        base = _mul(base, (-1, 0, 0, -1, w, h))
    elif rot == 270:
        base = _mul(base, (0, 1, -1, 0, h, 0))
        w, h = h, w
    return base, w, h


def extract(path, page_index: int = 0, tol: float = 0.1,
            reader=None) -> PageVectors:
    """All painted paths of page ``page_index`` (0-based). ``tol`` is the
    curve flattening tolerance in page points. ``reader``: an open reader
    of ``path`` to use (the background reading keeps one)."""
    reader = reader or _reader(path)
    if not 0 <= page_index < len(reader.pages):
        raise PdfImportError(f"The PDF has no page {page_index + 1}.")
    page = reader.pages[page_index]
    base, w, h = _page_base(page)

    out = PageVectors(width=w, height=h)
    from . import content_stream
    walk = content_stream.walk if content_stream.usable() else _walk_ops
    resources = page.get("/Resources")
    resources = resources.get_object() if resources is not None else {}
    contents = page.get_contents()
    if contents is not None:
        with gc_paused():
            walk((reader, out.paths, out.counts, tol, walk), contents,
                 resources, base, 0)
    return out


def _walk_ops(ctx, content, resources, ctm, depth):
    """Walk one content stream (and the Form XObjects inside it) over
    pypdf's operations: its painted paths into ``paths``, what is skipped
    into ``counts``. ``ctx`` = ``(reader, paths, counts, tol, walk)``,
    ``walk`` the function for the forms inside. The plain way —
    ``content_stream.walk`` gives the same, faster, and falls back to this
    for what it does not model."""
    if depth > 12:
        return
    reader, paths, counts, tol, walk = ctx
    from pypdf.generic import ContentStream

    def bump(k, n=1):
        counts[k] = counts.get(k, 0) + n

    try:
        ops = ContentStream(content, reader).operations
    except Exception:                                      # noqa: BLE001
        bump("unreadable streams")
        return
    gs = _State(ctm)
    stack = []
    sub = []           # subpaths: list of [runs]; run = [pts, curved]
    cur = None         # current point (user space)
    start = None
    in_text = False

    def P(x, y):
        return _apply(gs.ctm, float(x), float(y))

    def new_run(curved, first):
        sub[-1].append([[first], curved])

    def seg_to(pt, curved):
        if not sub:
            sub.append([])
        runs = sub[-1]
        if not runs or runs[-1][1] != curved:
            prev = runs[-1][0][-1] if runs else P(*cur)
            new_run(curved, prev)
        return sub[-1][-1][0]

    def paint(stroke, fill, evenodd=False):
        if sub:
            runs = [(pts, c) for r in sub for pts, c in r if len(pts) > 1]
            if runs:
                paths.append(PdfPath(
                    runs, stroke, fill, gs.srgb, gs.frgb,
                    gs.lw * _scale_of(gs.ctm), evenodd))
        sub.clear()

    for operands, op in ops:
        if isinstance(op, bytes):
            op = op.decode("latin-1")
        if in_text:
            if op == "ET":
                in_text = False
            elif op in ("Tj", "TJ", "'", '"'):
                bump("text")
            continue
        if op == "q":
            stack.append(gs.copy())
        elif op == "Q":
            if stack:
                gs = stack.pop()
        elif op == "cm":
            gs.ctm = _mul(tuple(float(v) for v in operands), gs.ctm)
        elif op == "w":
            gs.lw = float(operands[0])
        elif op in ("RG", "G", "K", "SC", "SCN"):
            c = _color(operands)
            if c:
                gs.srgb = c
        elif op in ("rg", "g", "k", "sc", "scn"):
            c = _color(operands)
            if c:
                gs.frgb = c
        elif op == "m":
            cur = (float(operands[0]), float(operands[1]))
            start = cur
            sub.append([])
            sub[-1].append([[P(*cur)], False])
        elif op == "l":
            if cur is None:
                continue
            nxt = (float(operands[0]), float(operands[1]))
            seg_to(nxt, False).append(P(*nxt))
            cur = nxt
        elif op in ("c", "v", "y"):
            if cur is None:
                continue
            v = [float(o) for o in operands]
            if op == "c":
                c1, c2, end = (v[0], v[1]), (v[2], v[3]), (v[4], v[5])
            elif op == "v":
                c1, c2, end = cur, (v[0], v[1]), (v[2], v[3])
            else:
                c1, c2, end = (v[0], v[1]), (v[2], v[3]), (v[2], v[3])
            pts = seg_to(end, True)
            pts.extend(_bezier(P(*cur), P(*c1), P(*c2), P(*end), tol))
            cur = end
        elif op == "h":
            if cur is not None and start is not None and sub:
                if math.dist(P(*cur), P(*start)) > 1e-9:
                    seg_to(start, False).append(P(*start))
                cur = start
        elif op == "re":
            x, y, rw, rh = (float(o) for o in operands)
            corners = [P(x, y), P(x + rw, y), P(x + rw, y + rh),
                       P(x, y + rh), P(x, y)]
            sub.append([[corners, False]])
            cur = start = (x, y)
        elif op in ("S", "s"):
            if op == "s" and cur is not None and start is not None \
                    and sub:
                seg_to(start, False).append(P(*start))
            paint(True, False)
        elif op in ("f", "F", "f*"):
            paint(False, True, op == "f*")
        elif op in ("B", "B*", "b", "b*"):
            if op in ("b", "b*") and cur is not None \
                    and start is not None and sub:
                if math.dist(P(*cur), P(*start)) > 1e-9:
                    seg_to(start, False).append(P(*start))
            paint(True, True, op.endswith("*"))
        elif op == "n":
            sub.clear()                       # clipping path only
        elif op == "BT":
            in_text = True
        elif op == "BI":
            bump("images")
        elif op == "sh":
            bump("shadings")
        elif op == "Do":
            name = operands[0]
            try:
                xo = resources["/XObject"][name].get_object()
            except Exception:                          # noqa: BLE001
                continue
            subtype = xo.get("/Subtype")
            if subtype == "/Form":
                m = tuple(float(v) for v in xo.get(
                    "/Matrix", (1, 0, 0, 1, 0, 0)))
                res = xo.get("/Resources")
                res = res.get_object() if res is not None else resources
                walk(ctx, xo, res, _mul(m, gs.ctm), depth + 1)
            elif subtype == "/Image":
                bump("images")


# ---- storey levels read from the sheets ----------------------------------
_LEVEL_NUM = r"(±|[+\-−])?\s?(\d{1,3})[,.](\d{2})"
_LEVEL_ONLY = re.compile(r"^" + _LEVEL_NUM + r"$")
_LEVEL_REL_ABS = re.compile(r"^" + _LEVEL_NUM + r"\s*=\s*[+\-−±]?\s?\d")
_STORY_LABEL = re.compile(r"^(-?\d{1,2})\s+(\S.{0,30})$")

# Imperial plans: levels in feet and inches «+10'-6"», «EL. 12'-0 1/2"»,
# «±0'-0"» — read as feet (the level unit of such a set).
_EL_PREFIX = r"(?:(?:EL(?:EV)?\.?|T\.?O\.?(?:\s*[A-Z]+)?\.?|F\.?F\.?[EL]?\.?)\s*:?\s*)?"
_LEVEL_FTIN = re.compile(
    r"^" + _EL_PREFIX + r"(±|[+\-−])?\s?(\d{1,4})\s*" + _FT_MARK
    + r"\s*-?\s*(?:(\d{1,2})(?:\s*[- ]\s*(\d{1,2})\s*/\s*(\d{1,2}))?"
    r"|(\d{1,2})\s*/\s*(\d{1,2}))?\s*" + _IN_MARK + r"?$", re.I)
_LEVEL_DEC = re.compile(r"^" + _EL_PREFIX + _LEVEL_NUM + r"\s*" + _FT_MARK
                        + r"?$", re.I)
# A feet-and-inches dimension anywhere: «12'-6"», «3' - 4 1/2"».
_FTIN_ANY = re.compile(r"\d\s*" + _FT_MARK + r"\s*-?\s*\d{1,2}(?:\s*[- ]\s*\d"
                       r"{1,2}\s*/\s*\d{1,2})?\s*(?:\"|”|″|'')")
_STORY_EN = re.compile(
    r"^(?:(?:basement|cellar|ground|first|second|third|fourth|fifth|sixth|"
    r"\d{1,2}(?:st|nd|rd|th))\s+(?:floor|level|story|storey)|"
    r"(?:level|floor|story|storey)\s*\d{1,2}|basement|cellar|attic|"
    r"mezzanine|roof|penthouse)\b.{0,24}$", re.I)


def level_value(text):
    """The number of a level text, in the unit it is written in (metres
    «+3,00», feet «+10'-6"» / «EL. 10.50'») — or None."""
    t = (text or "").strip()
    m = _LEVEL_ONLY.match(t)
    if m:
        return _level_value(m)
    m = _LEVEL_FTIN.match(t)
    if m and (m.group(3) is not None or re.search(_FT_MARK, t)):
        sign = m.group(1)
        ft = int(m.group(2))
        inch = 0.0
        if m.group(3):
            inch = int(m.group(3))
            if m.group(4) and m.group(5):
                inch += int(m.group(4)) / max(1, int(m.group(5)))
        elif m.group(6) and m.group(7):
            inch = int(m.group(6)) / max(1, int(m.group(7)))
        v = ft + inch / 12.0
        return -v if sign in ("-", "−") else v
    m = _LEVEL_DEC.match(t)
    if m:
        return _level_value(m)
    return None


def plan_unit(texts_by_page):
    """«imperial» when the sheets are written in feet and inches (scales
    «1/4" = 1'-0"», dimensions «12'-6"»), «metric» for «1:100» sets —
    None when nothing tells."""
    imp = met = 0
    for texts in texts_by_page.values():
        for t in texts:
            s = t.text or ""
            if imperial_scale(s) or _FTIN_ANY.search(s) \
                    or _LEVEL_FTIN.match(s.strip()) and "'" in s:
                imp += 1
            elif _SCALE_ANY.search(s) or _LEVEL_ONLY.match(s.strip()):
                met += 1
    if imp >= 3 and imp > met:
        return "imperial"
    if met:
        return "metric"
    return "imperial" if imp else None

_ABBR = (("untergeschoss", "ug"), ("kellergeschoss", "kg"),
         ("gartengeschoss", "gg"), ("souterrain", "sg"),
         ("sockelgeschoss", "sg"), ("erdgeschoss", "eg"),
         ("obergeschoss", "og"), ("dachgeschoss", "dg"),
         ("staffelgeschoss", "sg"), ("zwischengeschoss", "zg"),
         ("galerie", "ga"), ("spitzboden", "sb"))


def _level_value(m) -> float:
    sign, whole, frac = m.group(1), m.group(2), m.group(3)
    v = int(whole) + int(frac) / 100.0
    return -v if sign in ("-", "−") else v


_ORD = (("first", "1"), ("second", "2"), ("third", "3"), ("fourth", "4"),
        ("fifth", "5"), ("sixth", "6"), ("seventh", "7"), ("eighth", "8"))


def story_key(name: str) -> str:
    """«1. Obergeschoss», «1. OG», «1.OG» → «1og»; «Erdgeschoss» → «eg»;
    «Second Floor Plan», «2nd floor», «Level 2», «Floor 2» → «fl2»."""
    s = (name or "").lower()
    en = re.search(r"\b(floor|level|story|storey|basement|cellar|ground|"
                   r"attic|mezzanine|penthouse)\b", s)
    if en and not any(long in s for long, _sh in _ABBR):
        s = re.sub(r"\b(plan|plans|layout|drawing)\b", " ", s)
        for word, num in _ORD:
            s = re.sub(r"\b" + word + r"\b", num, s)
        s = re.sub(r"\b(\d+)(st|nd|rd|th)\b", r"\1", s)
        if re.search(r"\b(basement|cellar)\b", s):
            return "flb"
        if re.search(r"\bground\b", s):
            return "flg"
        m = re.search(r"\b(\d{1,2})\b", s)
        if m and re.search(r"\b(floor|level|story|storey)\b", s):
            return "fl" + m.group(1)
        return re.sub(r"[^a-z0-9]", "", s)
    for long, short in _ABBR:
        s = s.replace(long, short)
    return re.sub(r"[^a-z0-9]", "", s)


def _is_storey_name(name) -> bool:
    """«Erdgeschoss», «1. OG», «UG» — a floor plan, not a section."""
    s = (name or "").lower()
    if any(long in s for long, _short in _ABBR) or "geschoss" in s:
        return True
    if re.search(r"\b(floor|level|story|storey|basement|cellar|attic|"
                 r"mezzanine|penthouse)\b", s) and not re.search(
                     r"\b(section|elevation|detail|site|roof)\b", s):
        return True
    return bool(re.search(r"\b(ug|kg|gg|eg|og|dg|sg)\b", s))


def story_levels(texts) -> list:
    """Story markers as CAD sections/elevations print them — a level above
    a label «-1 GG», «0 EG», «1 1. OG» — as ``[(label, level_m)]``."""
    levels = [(t, level_value(t.text)) for t in texts]
    levels = [(t, v) for t, v in levels if v is not None]
    out = []
    for t in texts:
        txt = t.text.strip()
        if level_value(txt) is not None:
            continue
        ml = _STORY_LABEL.match(txt)
        en = None if ml else _STORY_EN.match(txt)
        if not ml and not en:
            continue
        best = None
        for lv, v in levels:
            dx = abs(lv.x - t.x)
            dy = lv.y - t.y
            # German sections: the level above its label; US elevation
            # markers: «LEVEL 2» with «EL. 12'-0"» above, below or beside
            near = (0 < dy < 2.5 * max(t.size, 1.0)) if ml else (
                abs(dy) < 2.5 * max(t.size, 1.0) and dx < 12 * t.size)
            if dx < (3 if ml else 12) * t.size and near:
                d = abs(dy) + 0.1 * dx
                if best is None or d < best[0]:
                    best = (d, v)
        if best is not None:
            out.append(((ml.group(2) if ml else txt).strip(), best[1]))
    return out


def page_level(texts):
    """A plan's own level marker «±0,00 = +571,30» (relative = absolute) —
    the most frequent one — or None."""
    found = {}
    for t in texts:
        txt = t.text.strip()
        m = _LEVEL_REL_ABS.match(txt)
        v = None
        if m:
            v = _level_value(m)
        elif "=" in txt and "'" in txt:
            v = level_value(txt.split("=", 1)[0])
        if v is not None:
            v = round(v, 3)
            found[v] = found.get(v, 0) + 1
    if not found:
        return None
    return max(found, key=lambda v: (found[v], -abs(v)))


def suggest_levels(names, texts_by_page):
    """Elevation suggestions per page index from the whole set.

    1. The story table found on sections/elevations («-1 GG -3,68») —
       matched to the page name (Gartengeschoss ↔ GG, 1. Obergeschoss ↔
       1. OG).
    2. Else the page's own «x = +abs» level marker, when there is exactly
       one kind of it or it matches a known story level.
    Returns ``({page: (level_m, source)}, typical_storey_height_or_None,
    [(label, level)] story table)``."""
    votes = {}
    for texts in texts_by_page.values():
        for label, lv in story_levels(texts):
            k = story_key(label)
            votes.setdefault(k, {}).setdefault(label, {})
            votes[k][label][lv] = votes[k][label].get(lv, 0) + 1
    table = {}
    for k, by_label in votes.items():
        label = max(by_label, key=lambda lb: sum(by_label[lb].values()))
        counts = {}
        for d in by_label.values():
            for lv, c in d.items():
                counts[lv] = counts.get(lv, 0) + c
        table[k] = (label, max(counts, key=counts.get))
    out = {}
    for i, name in names.items():
        k = story_key(name or "")
        if k and k in table:
            out[i] = (table[k][1], f"story {table[k][0]}")
            continue
        if not _is_storey_name(name):
            continue                    # sections, elevations, site plan
        own = page_level(texts_by_page.get(i, []))
        if own is not None:
            out[i] = (own, "level on the plan")
    levels = sorted(v for _l, v in table.values())
    diffs = [round(b - a, 2) for a, b in zip(levels, levels[1:])
             if b - a > 1.5]
    typical = max(set(diffs), key=diffs.count) if diffs else None
    story_list = sorted(((lb, v) for lb, v in table.values()),
                        key=lambda e: e[1])
    return out, typical, story_list


# ---- what is on a sheet: floor plan, elevation, section, site plan --------
_SIDES = (("N", ("nord", "north", "nordansicht")),
          ("S", ("süd", "sued", "sud", "south", "südansicht")),
          ("E", ("ost", "east", "ostansicht")),
          ("W", ("west", "westansicht")))
_SIDE_LETTER = {"n": "N", "s": "S", "o": "E", "e": "E", "w": "W"}


def classify_page(name, texts) -> tuple:
    """``(kind, sides, sure)`` — kind: "plan" | "site" | "elev" |
    "section" | "other"; sides: e.g. ["N"] or ["E", "W"] for elevations;
    sure: False when guessed from the content instead of the name."""
    s = (name or "").lower()
    words = re.findall(r"[a-zäöüß]+", s)
    if any(w.startswith(("ansicht", "elevation", "fassade", "facade"))
           for w in words):
        sides = []
        for side, keys in _SIDES:
            if any(k in s for k in keys):
                sides.append(side)
        if not sides:            # «Ansichten O W»: single letters
            for w in words[1:]:
                if len(w) == 1 and w in _SIDE_LETTER:
                    side = _SIDE_LETTER[w]
                    if side not in sides:
                        sides.append(side)
        return "elev", sides, bool(sides)
    if any(w.startswith(("schnitt", "section", "querschnitt",
                         "längsschnitt")) for w in words):
        return "section", [], True
    if any(w.startswith(("lageplan", "außenanlage", "aussenanlage",
                         "site", "freianlage")) for w in words):
        return "site", [], True
    if _is_storey_name(name):
        return "plan", [], True
    # nothing in the name: look at the content
    if len(story_levels(texts or [])) >= 2:
        return "elev", [], False             # storey markers → a view
    if page_level(texts or []) is not None:
        return "plan", [], False
    return "plan", [], False


def datum_y(texts):
    """Sheet y (points) of the ±0,00 storey line on an elevation/section —
    the story marker whose level reads 0 — or None."""
    ys = []
    for t in texts or []:
        m = _LEVEL_ONLY.match(t.text.strip())
        if m and abs(_level_value(m)) < 1e-9:
            # the level text sits on the storey line
            ys.append(t.y - 0.3 * max(t.size, 1.0))
    if not ys:
        return None
    ys.sort()
    return ys[len(ys) // 2]
