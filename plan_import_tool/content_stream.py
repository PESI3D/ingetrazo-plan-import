# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Pesi (pesi3d.de)
"""Content streams read fast — for pdf_vectors, no Qt.

pypdf reads a content stream token by token, about 3.5 MB/s; an A0 sheet
plotted from Revit carries 30 MB. Two short cuts, each giving exactly what
the pypdf route gives — which stays the fallback:

- ``walk``: the geometry walk of ``pdf_vectors.extract`` driven by one
  regular expression over the decoded stream, whole «x y m x y l S»
  statements at a time. Odd tokens are read by pypdf's own readers;
  anything this does not model goes to the plain walk over pypdf's
  operations (``pdf_vectors._walk_ops``).
- ``extract_text``: the text objects (BT … ET) and the few operators
  outside them that move text, cut out of the stream — pypdf's text
  extraction then reads kilobytes instead of megabytes.

Both lean on the vendored pypdf's details; ``usable()`` tells whether that
is the pypdf in use.
"""
from __future__ import annotations

import io
import math
import re

from . import pdf_vectors as pv

pypdf = pv._pypdf()
try:
    from pypdf._page import _get_page_resources
    from pypdf._utils import read_non_whitespace, read_until_regex
    from pypdf.generic import (ContentStream, FloatObject, NameObject,
                               NumberObject)
    from pypdf.generic._data_structures import IndirectPattern, read_object
    _IMPORTED = True
except ImportError:           # another pypdf, built otherwise: plain route
    _IMPORTED = False

#: The pypdf these short cuts were checked against (the vendored one).
PYPDF_VERSION = "6.19.0"


def usable() -> bool:
    return _IMPORTED and pypdf.__version__ == PYPDF_VERSION \
        and _RX is not None


# ---- tokens ---------------------------------------------------------------
# As pypdf reads them: operators and names end at white space or a
# delimiter, numbers are runs of «0-9 + , - .» (at most 64), white space
# between tokens includes NUL.
_DL = rb"\t\n\x0b\f\r ()<>\[\]{}/%"
_WS = rb"[\x00\t\n\f\r ]"
_HW = rb"[\t\n\f\r ]"
_NUM = rb"[0-9+\-.][0-9+,\-.]{0,46}+"
_NUMX = _NUM + rb"(?![0-9+,\-.])"
_END = rb"(?![^" + _DL + rb"])"
_OP = rb"[A-Za-z'\"][^" + _DL + rb"]{0,110}+" + _END
_NAME = rb"/[^" + _DL + rb"]{0,4078}+" + _END
_HEX = rb"<[0-9A-Fa-f\x00\t\n\f\r ]*+>"
_STR1 = rb"\((?:[^\x5c()]++|\x5c[\s\S])*+\)"
_STR = rb"\((?:[^\x5c()]++|\x5c[\s\S]|" + _STR1 + rb")*+\)"
_ARR = (rb"\[(?:[\t\n\x0b\f\r ]++|" + _NUMX + rb"|" + _STR + rb"|" + _HEX
        + rb")*+\]")
_N2 = rb"(" + _NUM + rb")" + _HW + rb"++(" + _NUM + rb")" + _HW + rb"++"
# The statements CAD plots consist of: «x y m x y l S» (groups 1-4) and
# «x y m x y l … S» (groups 1, 2 and the l-run in 5).
_HOT = (_N2 + rb"m" + _HW + rb"++(?:" + _N2 + rb"l" + _HW + rb"++S" + _END
        + rb"|((?:" + _NUM + _HW + rb"++" + _NUM + _HW + rb"++l" + _HW
        + rb"++)++)S" + _END + rb")")
try:
    # groups: 6 numbers before 7 an operator · 8 name · 9 numbers ·
    # 10 hex string · 11 string · 12 array · 13 comment · 14 anything else
    _RX = re.compile(
        _WS + rb"*+(?:" + _HOT
        + rb"|((?:" + _NUMX + _WS + rb"*+)*+)(" + _OP + rb")"
        + rb"|(" + _NAME + rb")"
        + rb"|((?:" + _NUMX + _WS + rb"*+)++)"
        + rb"|(" + _HEX + rb")|(" + _STR + rb")|(" + _ARR + rb")"
        + rb"|(%[^\r\n]*+)"
        + rb"|([\s\S]))")
    _TOK0 = (_WS + rb"++|" + _NUMX + rb"|" + _NAME + rb"|" + _HEX + rb"|"
             + _STR + rb"|" + _ARR)
    _TEXTBODY = re.compile(rb"(?:" + _TOK0 + rb"|(?!(?:ET|BI)" + _END + rb")"
                           + _OP + rb")*+")
    _ETOK = re.compile(_WS + rb"*+ET" + _END)
    _TEXTCOUNT = re.compile(rb"(?:" + _TOK0 + rb"|(Tj|TJ|'|\")" + _END
                            + rb"|" + _OP + rb")")
    _NUMTOK = re.compile(_NUM)
except re.error:              # possessive quantifiers need Python 3.11+
    _RX = None
_RNL = re.compile(rb"R[^a-zA-Z]")
_NEGZ = re.compile(rb"-0+(?![0-9+,\-.])")
_NUMCH = frozenset(b"0123456789+,-.")
_TEXTOPS = frozenset((b"Tj", b"TJ", b"'", b'"'))
_OPC = {b"q": 1, b"Q": 2, b"cm": 3, b"w": 4,
        b"RG": 5, b"G": 5, b"K": 5, b"SC": 5, b"SCN": 5,
        b"rg": 6, b"g": 6, b"k": 6, b"sc": 6, b"scn": 6,
        b"c": 7, b"v": 7, b"y": 7, b"re": 8,
        b"S": 9, b"s": 9, b"f": 10, b"F": 10, b"f*": 10,
        b"B": 11, b"B*": 11, b"b": 11, b"b*": 11,
        b"n": 12, b"BT": 13, b"sh": 14, b"Do": 15}
_USES = frozenset((3, 4, 5, 6, 7, 8, 15))       # take their operands


class _Lazy:
    """An operand pypdf reads only when it is used: at ``pos``."""
    __slots__ = ("pos",)

    def __init__(self, pos):
        self.pos = pos


class _ParseError(Exception):
    """pypdf could not have parsed the stream: «unreadable streams»."""


def _fnum(t):
    """A number token as pypdf's NumberObject / FloatObject, as float."""
    if b"." in t:
        try:
            return float(t)
        except ValueError:
            return 0.0
    try:
        return float(int(t))
    except ValueError:
        return 0.0


def _numobj(t):
    return FloatObject(t) if b"." in t else NumberObject(t)


def _mat(o, bio, fe):
    """A pending operand as the pypdf object."""
    if type(o) is bytes:
        if o[:1] == b"/":
            return NameObject.read_from_stream(io.BytesIO(o), None)
        return _numobj(o)
    if type(o) is _Lazy:
        bio.seek(o.pos)
        return read_object(bio, None, fe)
    return o


def _vals(pend, nums, has_nul, negz, bio, fe):
    """The operands of an operator: pending ones, then its numbers."""
    t = (_NUMTOK.findall(nums) if has_nul else nums.split()) if nums else ()
    if pend:
        return [_mat(o, bio, fe) for o in pend] + [_numobj(x) for x in t]
    if negz:
        return [_fnum(x) for x in t]
    try:
        return list(map(float, t))
    except ValueError:
        return [_fnum(x) for x in t]


def _has_indirect(data):
    """«1 0 R»: pypdf reads an object reference there, not two numbers."""
    s = IndirectPattern.search
    for m in _RNL.finditer(data):
        e = m.end()
        if s(data, max(0, e - 20), e) is not None:
            return True
    return False


def _has_negzero(data):
    """An integer «-0»: pypdf makes 0 of it, float() would make -0.0."""
    for m in _NEGZ.finditer(data):
        p = m.start()
        if p == 0 or data[p - 1] not in _NUMCH:
            return True
    return False


def _seg_to(sub, curved, ctm, cur):
    if not sub:
        sub.append([])
    runs = sub[-1]
    if not runs or runs[-1][1] != curved:
        prev = runs[-1][0][-1] if runs else \
            pv._apply(ctm, float(cur[0]), float(cur[1]))
        sub[-1].append([[prev], curved])
    return sub[-1][-1][0]


def _paint(sub, append, stroke, fill, srgb, frgb, width, evenodd):
    if sub:
        runs = [(pts, c) for r in sub for pts, c in r if len(pts) > 1]
        if runs:
            append(pv.PdfPath(runs, stroke, fill, srgb, frgb, width, evenodd))
    sub.clear()


def _slow(cs, bio, p, pend, fe):
    """One token at ``p`` exactly as pypdf's _parse_content_stream reads
    it. Returns ``(position after it, pending operands)``."""
    try:
        bio.seek(p)
        peek = read_non_whitespace(bio)
        if peek in (b"", 0):
            return bio.tell(), pend
        bio.seek(-1, 1)
        if peek.isalpha() or peek in (b"'", b'"'):
            operator = read_until_regex(
                stream=bio, regex=NameObject.delimiter_pattern,
                length=ContentStream._OPERATOR_LENGTH_LIMIT)
            if operator == b"BI":
                assert not pend
                cs._read_inline_image(bio)
                return bio.tell(), pend
            return bio.tell(), []         # an operator nobody handles
        if peek == b"%":
            while peek not in (b"\r", b"\n", b""):
                peek = bio.read(1)
            return bio.tell(), pend
        pend.append(read_object(bio, None, fe))
        return bio.tell(), pend
    except Exception as exc:
        raise _ParseError() from exc


def _parse_rest(cs, bio, p, fe):
    """After an error in the walk: does the rest of the stream parse? pypdf
    parses a stream whole before the walk starts — an error further on
    makes the stream unreadable, not the walk fail."""
    try:
        bio.seek(p)
        nops = 0
        while True:
            peek = read_non_whitespace(bio)
            if peek in (b"", 0):
                break
            bio.seek(-1, 1)
            if peek.isalpha() or peek in (b"'", b'"'):
                operator = read_until_regex(
                    stream=bio, regex=NameObject.delimiter_pattern,
                    length=ContentStream._OPERATOR_LENGTH_LIMIT)
                if operator == b"BI":
                    assert nops == 0
                    cs._read_inline_image(bio)
                else:
                    nops = 0
            elif peek == b"%":
                while peek not in (b"\r", b"\n", b""):
                    peek = bio.read(1)
            else:
                read_object(bio, None, fe)
                nops += 1
    except Exception as exc:
        raise _ParseError() from exc


def walk(ctx, content, resources, ctm, depth):
    """``pdf_vectors._walk_ops`` with the same result, fast. ``ctx`` =
    ``(reader, paths, counts, tol, walk)``."""
    if depth > 12:
        return
    _reader, paths, counts, _tol, _walk = ctx
    try:
        cs = ContentStream(content, _reader)
        data = cs.get_data()
    except Exception:                                      # noqa: BLE001
        counts["unreadable streams"] = counts.get("unreadable streams", 0) + 1
        return
    if _has_indirect(data):
        return pv._walk_ops(ctx, cs, resources, ctm, depth)
    n0 = len(paths)
    c0 = dict(counts)
    try:
        err = _walk_fast(ctx, cs, data, io.BytesIO(data), resources, ctm,
                         depth)
    except _ParseError:
        del paths[n0:]
        counts.clear()
        counts.update(c0)
        counts["unreadable streams"] = counts.get("unreadable streams", 0) + 1
        return
    if err is not None:
        raise err


def _walk_fast(ctx, cs, data, bio, resources, ctm, depth):
    """The walk itself. Returns the exception the plain walk would raise,
    or None; raises _ParseError when pypdf could not parse the stream."""
    _reader, paths, counts, tol, walk_ = ctx
    append = paths.append
    PdfPath = pv.PdfPath
    _scale_of = pv._scale_of
    _apply = pv._apply
    fe = cs.forced_encoding
    a, b, c, d, e, f = ctm
    srgb = frgb = (0.0, 0.0, 0.0)
    lw = 1.0
    width = None                  # lw × CTM scale, while both stay
    stack = []
    sub = []
    cur = start = None
    in_text = False
    pend = []
    has_nul = b"\x00" in data
    negz = _has_negzero(data)
    exact = has_nul or negz
    finditer = _RX.finditer
    pos = 0
    mt = None
    while True:
        slow = -1
        try:
            for mt in finditer(data, pos):
                k = mt.lastindex
                if k == 4:                                 # x y m x y l S
                    if in_text:
                        if pend:
                            pend = []
                        continue
                    g = mt.group(1, 2, 3, 4)
                    if pend:
                        v = [_mat(o, bio, fe) for o in pend] + \
                            [_numobj(g[0]), _numobj(g[1])]
                        pend = []
                        x0 = float(v[0])
                        y0 = float(v[1])
                        x1 = _fnum(g[2])
                        y1 = _fnum(g[3])
                    elif negz:
                        x0, y0, x1, y1 = map(_fnum, g)
                    else:
                        try:
                            x0, y0, x1, y1 = map(float, g)
                        except ValueError:
                            x0, y0, x1, y1 = map(_fnum, g)
                    pts = [(a * x0 + c * y0 + e, b * x0 + d * y0 + f),
                           (a * x1 + c * y1 + e, b * x1 + d * y1 + f)]
                    cur = (x1, y1)
                    start = (x0, y0)
                    if width is None:
                        width = lw * _scale_of(ctm)
                    if sub:
                        sub.append([[pts, False]])
                        _paint(sub, append, True, False, srgb, frgb, width,
                               False)
                    else:
                        append(PdfPath([(pts, False)], True, False, srgb,
                                       frgb, width, False))
                    continue
                if k == 7:                                 # numbers operator
                    nums, op = mt.group(6, 7)
                    if in_text:
                        if op == b"ET":
                            in_text = False
                        elif op in _TEXTOPS:
                            counts["text"] = counts.get("text", 0) + 1
                        elif op == b"BI":
                            if pend or nums:
                                raise _ParseError()
                            slow = mt.start(7)
                            break
                        if pend:
                            pend = []
                        continue
                    if op == b"l":
                        if cur is not None:
                            if pend or exact:
                                v = _vals(pend, nums, has_nul, negz, bio, fe)
                                pend = []
                                x = float(v[0])
                                y = float(v[1])
                            else:
                                t = nums.split()
                                try:
                                    x = float(t[0])
                                    y = float(t[1])
                                except ValueError:
                                    x = _fnum(t[0])
                                    y = _fnum(t[1])
                            pt = (a * x + c * y + e, b * x + d * y + f)
                            if sub and sub[-1] and sub[-1][-1][1] is False:
                                sub[-1][-1][0].append(pt)
                            else:
                                _seg_to(sub, False, ctm, cur).append(pt)
                            cur = (x, y)
                        elif pend:
                            pend = []
                        continue
                    if op == b"m":
                        if pend or exact:
                            v = _vals(pend, nums, has_nul, negz, bio, fe)
                            pend = []
                            x = float(v[0])
                            y = float(v[1])
                        else:
                            t = nums.split()
                            try:
                                x = float(t[0])
                                y = float(t[1])
                            except ValueError:
                                x = _fnum(t[0])
                                y = _fnum(t[1])
                        cur = start = (x, y)
                        sub.append([[[(a * x + c * y + e, b * x + d * y + f)],
                                     False]])
                        continue
                    if op == b"h":
                        if pend:
                            pend = []
                        if cur is not None and start is not None and sub:
                            pc = _apply(ctm, cur[0], cur[1])
                            ps = _apply(ctm, start[0], start[1])
                            if math.dist(pc, ps) > 1e-9:
                                _seg_to(sub, False, ctm, cur).append(ps)
                            cur = start
                        continue
                    if op == b"BI":
                        if pend or nums:
                            raise _ParseError()
                        slow = mt.start(7)
                        break
                    code = _OPC.get(op)
                    if code is None:
                        if pend:
                            pend = []
                        continue
                    if code in _USES:
                        v = _vals(pend, nums, has_nul, negz, bio, fe)
                    if pend:
                        pend = []
                    if code == 9:                          # S s
                        if op == b"s" and cur is not None \
                                and start is not None and sub:
                            _seg_to(sub, False, ctm, cur).append(
                                _apply(ctm, start[0], start[1]))
                        if width is None:
                            width = lw * _scale_of(ctm)
                        _paint(sub, append, True, False, srgb, frgb, width,
                               False)
                    elif code == 10:                       # f F f*
                        if width is None:
                            width = lw * _scale_of(ctm)
                        _paint(sub, append, False, True, srgb, frgb, width,
                               op == b"f*")
                    elif code == 5:
                        col = pv._color(v)
                        if col:
                            srgb = col
                    elif code == 6:
                        col = pv._color(v)
                        if col:
                            frgb = col
                    elif code == 4:
                        lw = float(v[0])
                        width = None
                    elif code == 13:                       # BT: skip it whole
                        s0 = mt.end()
                        q_ = _TEXTBODY.match(data, s0).end()
                        em = _ETOK.match(data, q_)
                        if em is not None:
                            L = _TEXTCOUNT.findall(data, s0, q_)
                            nt = len(L) - L.count(b"")
                            if nt:
                                counts["text"] = counts.get("text", 0) + nt
                            pos = em.end()
                            break
                        in_text = True
                    elif code == 1:
                        stack.append((ctm, srgb, frgb, lw))
                    elif code == 2:
                        if stack:
                            ctm, srgb, frgb, lw = stack.pop()
                            a, b, c, d, e, f = ctm
                            width = None
                    elif code == 3:
                        ctm = pv._mul(tuple(float(x) for x in v), ctm)
                        a, b, c, d, e, f = ctm
                        width = None
                    elif code == 11:                       # B B* b b*
                        if op in (b"b", b"b*") and cur is not None \
                                and start is not None and sub:
                            ps = _apply(ctm, float(start[0]), float(start[1]))
                            if math.dist(_apply(ctm, float(cur[0]),
                                                float(cur[1])), ps) > 1e-9:
                                _seg_to(sub, False, ctm, cur).append(ps)
                        if width is None:
                            width = lw * _scale_of(ctm)
                        _paint(sub, append, True, True, srgb, frgb, width,
                               op.endswith(b"*"))
                    elif code == 12:
                        sub.clear()
                    elif code == 7:                        # c v y
                        if cur is not None:
                            vv = [float(o) for o in v]
                            if op == b"c":
                                c1, c2, end = ((vv[0], vv[1]), (vv[2], vv[3]),
                                               (vv[4], vv[5]))
                            elif op == b"v":
                                c1, c2, end = cur, (vv[0], vv[1]), \
                                    (vv[2], vv[3])
                            else:
                                c1, c2, end = ((vv[0], vv[1]), (vv[2], vv[3]),
                                               (vv[2], vv[3]))
                            pts = _seg_to(sub, True, ctm, cur)
                            pts.extend(pv._bezier(
                                _apply(ctm, float(cur[0]), float(cur[1])),
                                _apply(ctm, float(c1[0]), float(c1[1])),
                                _apply(ctm, float(c2[0]), float(c2[1])),
                                _apply(ctm, float(end[0]), float(end[1])),
                                tol))
                            cur = end
                    elif code == 8:                        # re
                        x, y, rw, rh = (float(o) for o in v)
                        corners = [_apply(ctm, x, y),
                                   _apply(ctm, float(x + rw), y),
                                   _apply(ctm, float(x + rw), float(y + rh)),
                                   _apply(ctm, x, float(y + rh)),
                                   _apply(ctm, x, y)]
                        sub.append([[corners, False]])
                        cur = start = (x, y)
                    elif code == 14:
                        counts["shadings"] = counts.get("shadings", 0) + 1
                    elif code == 15:                       # Do
                        name = v[0]
                        try:
                            xo = resources["/XObject"][name].get_object()
                        except Exception:                  # noqa: BLE001
                            continue
                        subtype = xo.get("/Subtype")
                        if subtype == "/Form":
                            m_ = tuple(float(x) for x in xo.get(
                                "/Matrix", (1, 0, 0, 1, 0, 0)))
                            res = xo.get("/Resources")
                            res = res.get_object() if res is not None \
                                else resources
                            walk_(ctx, xo, res, pv._mul(m_, ctm), depth + 1)
                        elif subtype == "/Image":
                            counts["images"] = counts.get("images", 0) + 1
                    continue
                if k == 5:                                 # x y m (x y l)+ S
                    if in_text:
                        if pend:
                            pend = []
                        continue
                    g1, g2, lp = mt.group(1, 2, 5)
                    t = lp.split()
                    tx = t[0::3]
                    ty = t[1::3]
                    if pend:
                        v = [_mat(o, bio, fe) for o in pend] + \
                            [_numobj(g1), _numobj(g2)]
                        pend = []
                        x0 = float(v[0])
                        y0 = float(v[1])
                        xs = list(map(_fnum, tx))
                        ys = list(map(_fnum, ty))
                    elif negz:
                        x0 = _fnum(g1)
                        y0 = _fnum(g2)
                        xs = list(map(_fnum, tx))
                        ys = list(map(_fnum, ty))
                    else:
                        try:
                            x0 = float(g1)
                            y0 = float(g2)
                            xs = list(map(float, tx))
                            ys = list(map(float, ty))
                        except ValueError:
                            x0 = _fnum(g1)
                            y0 = _fnum(g2)
                            xs = list(map(_fnum, tx))
                            ys = list(map(_fnum, ty))
                    pts = [(a * x0 + c * y0 + e, b * x0 + d * y0 + f)]
                    pts += [(a * x + c * y + e, b * x + d * y + f)
                            for x, y in zip(xs, ys)]
                    cur = (xs[-1], ys[-1])
                    start = (x0, y0)
                    if width is None:
                        width = lw * _scale_of(ctm)
                    if sub:
                        sub.append([[pts, False]])
                        _paint(sub, append, True, False, srgb, frgb, width,
                               False)
                    else:
                        append(PdfPath([(pts, False)], True, False, srgb,
                                       frgb, width, False))
                    continue
                if k == 8:                                 # name
                    pend.append(mt.group(8))
                elif k == 9:                               # numbers
                    g = mt.group(9)
                    pend.extend(_NUMTOK.findall(g) if has_nul else g.split())
                elif k == 14:                              # anything else
                    slow = mt.start(14)
                    break
                elif k != 13:                    # 10 hex, 11 string, 12 array
                    pend.append(_Lazy(mt.start(k)))
            else:
                return None
        except _ParseError:
            raise
        except Exception as exc:                           # noqa: BLE001
            _parse_rest(cs, bio, mt.end(), fe)
            return exc
        if slow >= 0:
            pos, pend = _slow(cs, bio, slow, pend, fe)


# ---- texts --------------------------------------------------------------
class Fallback(Exception):
    """Something the text cut does not model: use the whole stream."""


_SP = frozenset(b"\x00\t\n\x0c\r ")
_REG = frozenset(range(256)) - _SP - frozenset(b"()<>[]{}/%")
_ALPHA = frozenset(b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz")
_NUMCHARS = frozenset(b"+-.0123456789")
_CAND = re.compile(rb"\(|<|%|BT|BI|ET|q|Q|cm|Do|T[zwLfdDm*jJ]|\x27|\x22")
_TSTR = re.compile(rb"\([^()\x5c]*(?:\x5c.[^()\x5c]*)*\)", re.S)
_THEX = re.compile(rb"<[0-9A-Fa-f\x00\t\n\x0c\r ]*>")
_EOL = re.compile(rb"[\r\n]")
# Outside text objects pypdf's text extraction reacts to q Q cm and these:
_NUMOPS = frozenset((b"cm", b"Do", b"Tf", b"Tz", b"Tw", b"TL", b"Td", b"TD",
                     b"Tm"))
_BARE = frozenset((b"q", b"Q", b"T*", b"ET"))


def _string_end(data, i):
    m = _TSTR.match(data, i)
    if m:
        return m.end()
    depth = 0
    n = len(data)
    j = i
    while j < n:
        ch = data[j]
        if ch == 92:                      # backslash: skip the next byte
            j += 2
            continue
        if ch == 40:
            depth += 1
        elif ch == 41:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise Fallback("unterminated string")


def _operands_start(data, s):
    """Where the plain numbers and names before the operator at ``s``
    begin."""
    j = s
    while True:
        while j and data[j - 1] in _SP:
            j -= 1
        if not j:
            return 0
        k = j
        while k and data[k - 1] in _REG:
            k -= 1
        if k and data[k - 1] == 47:       # a name
            j = k - 1
            continue
        tok = data[k:j]
        if not tok:
            raise Fallback("non-simple operand")
        if all(ch in _NUMCHARS for ch in tok):
            j = k
            continue
        if tok[0] in _ALPHA or tok[0] in (39, 34):
            return j
        raise Fallback("odd token before an operator")


def text_operators(data: bytes) -> bytes:
    """The bytes of every text object (BT … ET, verbatim) and of the q, Q,
    cm, Do and text state operators outside them, one per line — all that
    pypdf's text extraction reacts to. Raises Fallback otherwise."""
    out = []
    app = out.append
    n = len(data)
    pos = 0
    in_bt = False
    bt0 = 0
    search = _CAND.search
    while True:
        m = search(data, pos)
        if m is None:
            break
        s, e = m.span()
        ch = data[s]
        if ch == 40:                      # ( string
            pos = _string_end(data, s)
            continue
        if ch == 60:                      # < hex string, << dictionary
            if data[s + 1:s + 2] == b"<":
                pos = s + 2
                continue
            mh = _THEX.match(data, s)
            if mh is None:
                raise Fallback("bad hex string")
            pos = mh.end()
            continue
        if ch == 37:                      # % comment
            me = _EOL.search(data, s)
            pos = me.start() if me else n
            continue
        op = data[s:e]
        if in_bt and op != b"ET" and op != b"BI":
            pos = e
            continue
        if e < n and data[e] in _REG:     # only the start of a longer token
            pos = s + 1
            continue
        prev = data[s - 1] if s else 32
        if prev in _REG or prev == 47:
            k = s
            while k and data[k - 1] in _REG:
                k -= 1
            if (k and data[k - 1] == 47) or \
                    not all(c in _NUMCHARS for c in data[k:s]):
                pos = e                   # the end of a longer token
                continue
            raise Fallback("number glued to an operator")
        if prev not in _SP and prev not in (41, 62, 93):
            raise Fallback("odd token boundary")
        if in_bt:
            if op == b"ET":
                app(data[bt0:e])
                in_bt = False
            else:
                raise Fallback("inline image in a text object")
        elif op == b"BT":
            in_bt = True
            bt0 = s
        elif op in _BARE:
            app(op)
        elif op in _NUMOPS:
            app(data[_operands_start(data, s):e])
        else:
            raise Fallback(op.decode("latin-1") + " outside BT")
        pos = e
    if in_bt:
        app(data[bt0:])
    return b"\n".join(out)


def extract_text(reader, page, visit):
    """``page.extract_text(visitor_text=visit)``, from the text operators
    only — the page itself stays untouched. Raises Fallback when the
    stream holds what the cut does not model."""
    contents = page.get_contents()
    reduced = text_operators(contents.get_data()) if contents is not None \
        else b""
    # forced encoding «bytes», as extract_text reads a page
    cs = ContentStream(None, reader, "bytes")
    cs.set_data(reduced)
    cs[NameObject("/Resources")] = _get_page_resources(page)
    # extract_xform_text has other default orientations: extract_text's
    return page.extract_xform_text(cs, (0, 90, 180, 270), 200.0,
                                   visitor_text=visit)
