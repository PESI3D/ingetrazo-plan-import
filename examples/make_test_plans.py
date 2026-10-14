"""Synthetic vector floor plans for testing the Plan Import plugin.

grundriss_1-100_A3.pdf  — 10.00 x 8.00 m house at 1:100 on A3 landscape:
    filled grey walls (36.5 / 11.5 cm), door swings (arcs), window lines,
    a dimension chain with text, hatch lines, a plan header box, a white
    background rectangle. Known check: outer walls = 10.00 m x 8.00 m.
grundriss_1-50_A4_2pages.pdf — same house at 1:50, 2 pages (page 2 = a
    simple 4 x 3 m room drawn with stroked lines only).
"""
import math
import sys
from pathlib import Path

from reportlab.lib.pagesizes import A3, A4, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas


def house(c, ox, oy, s):
    """Draw the house; s = paper mm per real metre (1:100 -> 10)."""
    def P(x, y):                       # real metres -> paper points
        return ox + x * s * mm, oy + y * s * mm

    # white background (should be skipped by "Skip white")
    c.setFillColorRGB(1, 1, 1)
    x0, y0 = P(-1, -1)
    c.rect(x0, y0, 12 * s * mm, 10 * s * mm, stroke=0, fill=1)

    # walls: outer ring 10 x 8 m, thickness 0.365, filled grey + black outline
    c.setFillColorRGB(0.6, 0.6, 0.6)
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.5)
    t = 0.365
    p = c.beginPath()
    p.moveTo(*P(0, 0)); p.lineTo(*P(10, 0)); p.lineTo(*P(10, 8))
    p.lineTo(*P(0, 8)); p.close()
    p.moveTo(*P(t, t)); p.lineTo(*P(t, 8 - t)); p.lineTo(*P(10 - t, 8 - t))
    p.lineTo(*P(10 - t, t)); p.close()
    c.drawPath(p, stroke=1, fill=1, fillMode=0)        # even-odd ring

    # inner wall 11.5 cm at x = 6 m
    w = 0.115
    c.rect(*P(6, t), w * s * mm, (8 - 2 * t) * s * mm, stroke=1, fill=1)

    # door swing in inner wall: leaf line + quarter arc, radius 0.885 m
    c.setLineWidth(0.25)
    hx, hy = P(6, 2)
    r = 0.885 * s * mm
    c.line(hx, hy, hx - r, hy)
    c.arc(hx - r, hy - r, hx + r, hy + r, startAng=90, extent=90)

    # windows in the south wall: 3 thin lines across the wall each
    for wx in (2.0, 7.5):
        for k in (0.0, 0.18, 0.365):
            c.line(*P(wx, k), *P(wx + 1.26, k))

    # hatch in the west wall section (45 degree lines)
    c.setLineWidth(0.1)
    for i in range(12):
        y = 1 + i * 0.25
        c.line(*P(0, y), *P(t, y + t))

    # dimension chain under the house with text
    c.setLineWidth(0.18)
    c.line(*P(0, -0.6), *P(10, -0.6))
    for x in (0, 2, 3.26, 10):
        c.line(*P(x, -0.7), *P(x, -0.5))
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 7)
    c.drawString(*P(4.6, -0.5), "10,00")

    # a circle (column) and a bezier-heavy shape (round table)
    c.circle(*P(3, 4), 0.4 * s * mm, stroke=1, fill=0)


def header(c, W, H):
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.7)
    c.rect(W - 190 * mm, 10 * mm, 180 * mm, 40 * mm, stroke=1, fill=0)
    c.setFont("Helvetica-Bold", 14)
    c.drawString(W - 185 * mm, 38 * mm, "Testgrundriss EG")
    c.setFont("Helvetica", 9)
    c.drawString(W - 185 * mm, 28 * mm, "M 1:100  -  Plan Import Test")


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    W, H = landscape(A3)
    c = canvas.Canvas(str(out / "grundriss_1-100_A3.pdf"), pagesize=(W, H))
    house(c, 60 * mm, 80 * mm, 10)      # 1:100 -> 10 mm per metre
    header(c, W, H)
    c.showPage()
    c.save()

    W, H = landscape(A4)
    c = canvas.Canvas(str(out / "grundriss_1-50_A4_2pages.pdf"),
                      pagesize=(W, H))
    house(c, 20 * mm, 30 * mm, 20)      # 1:50 -> 20 mm per metre
    c.showPage()
    c.setLineWidth(0.5)                 # page 2: 4 x 3 m room, strokes only
    c.rect(40 * mm, 40 * mm, 80 * mm, 60 * mm, stroke=1, fill=0)
    c.showPage()
    c.save()


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "Testdateien"))
