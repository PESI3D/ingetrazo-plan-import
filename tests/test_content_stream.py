# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Pesi (pesi3d.de)
"""The content_stream short cuts must give exactly what the plain pypdf
route gives: the same paths, counts and errors; the same texts."""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from plan_import_tool import content_stream as cs   # noqa: E402
from plan_import_tool import pdf_vectors as pv      # noqa: E402
from pypdf.generic import (ArrayObject, ContentStream,  # noqa: E402
                           DecodedStreamObject, DictionaryObject,
                           FloatObject, NameObject, NumberObject)

EXAMPLES = os.path.join(os.path.dirname(HERE), "examples")

#: Synthetic streams: what CAD plots hold, and what they rarely do.
STREAMS = [
    b"0 0 m 10 10 l S",
    b"1 0 0 RG 2 w 0 0 m 10 0 l 10 10 l S 5 5 m 6 6 l S",
    b"q 2 0 0 2 10 20 cm 0 0 m 1 1 l S Q 0 0 m 1 1 l S",
    b"0 0 m 10 0 l 10 10 l h f 0 0 10 10 re B* 1 1 m 2 2 l b",
    b"0 0 m 1 2 3 4 5 6 c 7 8 9 10 v 11 12 13 14 y S",
    b"0 0 m 5 5 l W n 1 1 m 2 2 l S",
    b"0 0 m 1 1 l 2 2 l 3 3 l S 4 4 m 5 5 l 6 6 l S 7 7 m 8 8 l",
    b"0 0 m 1 1 l s 0 0 m 1 1 l 2 0 l b* 3 3 m 4 4 l B",
    b"0.5 g 0 0 m 1 0 l 1 1 l f 0.2 0.4 0.6 rg 0 0 1 1 re f* "
    b"0 1 0 0 k 0 0 1 1 re F 0.1 G 0 0 m 1 1 l S 1 0 0 0 K 0 0 m 1 1 l S",
    b"/CS0 cs 0.3 0.3 0.3 scn /P1 scn /CS1 CS 0.7 SC 0 0 m 1 1 l S",
    b"BT /F1 12 Tf (a ET b) Tj [(A) -250 (B)] TJ (x) ' 1 2 (y) \" ET "
    b"0 0 m 1 1 l S",
    b"BT (x\\) ET) Tj ET 0 0 m 1 1 l S",
    b"BT (a (nested (deep ET) x) b) Tj ET 3 3 m 4 4 l S",
    b"BT <48 49> Tj <4 8> Tj ET 0 0 m 1 1 l S",
    b"/P <</MCID 0 /ActualText (BT junk ET)>> BDC 0 0 m 1 1 l S EMC",
    b"/OC /MC0 BDC 0 0 m 1 1 l S EMC /Span <</Lang (de) /E [1 (x)]>> BDC "
    b"2 2 m 3 3 l S EMC",
    b"0 0 m%comment\n1 1 l S",
    b"0 0%c\nm 1 1 l S % trailing",
    b".5 -.5 m 5. +3 l S",
    b"4.-3 1.2.3 m --5 1 l S 1,5 2 m 3 4 l S",
    b"1 2 3 4 5 6cm 0 0 m 1 1 l S",
    b"[1 2]0 d 0 0 m 1 1 l S [] 0 d 2 2 m 3 3 l S",
    b"0 0 m 1 1 l S\rq\r1 0 0 1 5 5 cm\r0 0 m 2 2 l S\rQ\r\n",
    b"0\x000 m 1 1\x00l S",
    b"-0 0 m 1 -0 l S",
    b"-0.0 0 m 1 -00 l S 0 0 m -0 1 l 2 2 l S",
    b"1 0 R 0 0 m 1 1 l S",
    b"BI /W 2 /H 1 /BPC 8 /CS /G ID \x00E\xffEI 0 0 m 1 1 l S",
    b"0 0 m 1 1 l S BI /W 4 /H 1 /BPC 8 /CS /G ID EIEI EI 2 2 m 3 3 l S",
    b"BT BI /W 1 /H 1 /BPC 8 /CS /G ID x EI ET 0 0 m 1 1 l S",
    b"0 0 m 1 1 l S (unterminated",
    b"0 0 m 1 1 l S <zz>",
    b"0 0 m 1 1 l S BT (a) Tj",
    b"m 1 1 l S",
    b"/Name 0 0 m 1 1 l S",
    b"(str) 0 0 m 1 1 l S",
    b"0 0 m 1 1 l S EI ID",
    b"0 0 m 1 1 l S (a) Tj",
    b"\x0b0 0 m 1 1 l S",
    b"0 0 m 1 1 l S true false null",
    b"0 0 m 1 1 l Sx 2 2 m 3 3 l S",
    b"0 0 m 1 1 l S0 0 m 3 3 l S",
    b"1 0 0 1 0 0 cm 0 0 m 1 1 l S /Sh1 sh",
    b"0 0 m 1 1 l S 1 2 3 cm 0 0 m 1 1 l S",
    b"0 0 m (x) l S",
    b"0 0 m 1 1 l S 2 2 m 3 3 l 0 w S 0.25 w 4 4 m 5 5 l S",
    b"q q q 0 0 m 1 1 l S Q Q Q Q Q 0 0 m 1 1 l S",
    b"<< /A 1 >> 0 0 m 1 1 l S",
    b"0 0 m " + b"1 1 l " * 2000 + b"S",
    b"0" * 70 + b" 0 m 1 1 l S",
    b"x" * 140 + b" 0 0 m 1 1 l S",
]


def stream(data):
    so = DecodedStreamObject()
    so.set_data(data)
    return so


def run(walk, data, resources=None, ctm=(1, 0, 0, 1, 0, 0)):
    """``(paths, counts, None)`` or ``(None, None, error type)``."""
    paths, counts = [], {}
    try:
        walk((None, paths, counts, 0.1, walk), stream(data),
             {} if resources is None else resources, ctm, 0)
    except Exception as exc:                               # noqa: BLE001
        return None, None, type(exc)
    return paths, counts, None


def extract_with(walk, path, i):
    """pdf_vectors.extract with the walk given."""
    reader = pv.open_reader(path)
    page = reader.pages[i]
    base, w, h = pv._page_base(page)
    out = pv.PageVectors(width=w, height=h)
    res = page.get("/Resources")
    res = res.get_object() if res is not None else {}
    contents = page.get_contents()
    if contents is not None:
        walk((reader, out.paths, out.counts, 0.1, walk), contents, res,
             base, 0)
    return out


def examples():
    for name in sorted(os.listdir(EXAMPLES)):
        if name.endswith(".pdf"):
            path = os.path.join(EXAMPLES, name)
            for i in range(len(pv.page_info(path))):
                yield name, path, i


class WalkTest(unittest.TestCase):
    def assertSameWalk(self, data, resources=None, ctm=(1, 0, 0, 1, 0, 0)):
        want = run(pv._walk_ops, data, resources, ctm)
        got = run(cs.walk, data, resources, ctm)
        self.assertEqual(want, got, data[:80])
        if want[0] is not None:       # same counters in the same order
            self.assertEqual(list(want[1]), list(got[1]), data[:80])

    def test_usable(self):
        self.assertTrue(cs.usable())

    def test_streams(self):
        for data in STREAMS:
            self.assertSameWalk(data)
            self.assertSameWalk(data, ctm=(0, -2, 2, 0, 5, 700))

    def test_forms(self):
        form = DecodedStreamObject()
        form.set_data(b"0 0 m 10 0 l S BT (t) Tj ET /Fm1 Do 1 1 m 2 2 l S")
        form[NameObject("/Subtype")] = NameObject("/Form")
        form[NameObject("/Matrix")] = ArrayObject(
            [FloatObject(0.5), NumberObject(0), NumberObject(0),
             FloatObject(0.5), NumberObject(5), NumberObject(5)])
        img = DecodedStreamObject()
        img[NameObject("/Subtype")] = NameObject("/Image")
        xobjects = DictionaryObject({NameObject("/Fm1"): form,
                                     NameObject("/Im1"): img})
        res = DictionaryObject({NameObject("/XObject"): xobjects})
        for data in (b"q 1 0 0 1 3 3 cm /Fm1 Do Q /Fm1 Do",
                     b"/Im1 Do /Missing Do 0 0 m 1 1 l S",
                     b"/Fm1 Do (unterminated"):
            self.assertSameWalk(data, res)

    def test_examples(self):
        for name, path, i in examples():
            want = extract_with(pv._walk_ops, path, i)
            got = extract_with(cs.walk, path, i)
            self.assertEqual(want, got, f"{name} p{i + 1}")

    def test_extract_uses_it(self):
        name, path, i = next(examples())
        with mock.patch.object(cs, "usable", return_value=False):
            want = pv.extract(path, i)
        self.assertEqual(want, pv.extract(path, i))


#: Operators pypdf's text extraction reacts to (pypdf 6.19).
HANDLED = frozenset((b"BT", b"ET", b"q", b"Q", b"cm", b"Tz", b"Tw", b"TL",
                     b"Tf", b"Td", b"Tm", b"T*", b"Tj", b"TJ", b"'", b'"',
                     b"TD", b"Do"))


def text_ops(ops):
    """The operations of whole text objects and the handled ones outside."""
    out, inside = [], False
    for t in ops:
        op = t[1]
        if inside:
            out.append(t)
            inside = op != b"ET"
        elif op == b"BT":
            inside = True
            out.append(t)
        elif op in HANDLED:
            out.append(t)
    return out


def parse(data):
    c = ContentStream(None, None, "bytes")
    c.set_data(data)
    return c.operations


class TextTest(unittest.TestCase):
    def test_streams(self):
        fell_back = 0
        for data in STREAMS:
            try:
                full = text_ops(parse(data))
            except Exception:                              # noqa: BLE001
                full = None           # pypdf cannot read it: fall back
            try:
                red = cs.text_operators(data)
            except cs.Fallback:
                fell_back += 1
                continue
            if full is None:
                continue
            got = parse(red)
            self.assertEqual(len(full), len(got), data[:80])
            for (oa, pa), (ob, pb) in zip(full, got):
                self.assertEqual(pa, pb, data[:80])
                self.assertEqual([(type(x), x) for x in oa],
                                 [(type(x), x) for x in ob], data[:80])
        self.assertTrue(0 < fell_back < len(STREAMS))

    def test_examples(self):
        for name, path, i in examples():
            with mock.patch.object(cs, "usable", return_value=False):
                want = pv.page_texts(path, i)
            got = pv.page_texts(path, i)
            self.assertEqual(want[:2], got[:2], f"{name} p{i + 1}")
            self.assertEqual([repr(t) for t in want[2]],
                             [repr(t) for t in got[2]], f"{name} p{i + 1}")

    def test_page_untouched(self):
        name, path, i = next(examples())
        reader = pv.open_reader(path)
        page = reader.pages[i]
        pv._page_base(page)           # pypdf caches the CropBox here
        before = dict(page)
        pv.page_texts(path, i, reader)
        self.assertEqual(list(before), list(page))
        self.assertTrue(all(before[k] is dict.__getitem__(page, k)
                            for k in before))


class GcPausedTest(unittest.TestCase):
    def test_nesting(self):
        import gc
        was = gc.isenabled()
        gc.enable()
        try:
            with pv.gc_paused():
                self.assertFalse(gc.isenabled())
                with pv.gc_paused():
                    self.assertFalse(gc.isenabled())
                self.assertFalse(gc.isenabled())
            self.assertTrue(gc.isenabled())
            gc.disable()
            with pv.gc_paused():
                pass
            self.assertFalse(gc.isenabled())   # left as it was
        finally:
            (gc.enable if was else gc.disable)()


if __name__ == "__main__":
    unittest.main()
