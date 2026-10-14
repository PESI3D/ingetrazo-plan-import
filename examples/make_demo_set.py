"""Demo plan set «Musterhaus» (fictional): 5 sheets A3 landscape 1:100 with
sheet frame and title block — Erdgeschoss, Obergeschoss, Ansichten N S
(two elevations on one sheet), Ansichten O W, Schnitt A-A.
House 12.00 x 9.00 m, EG ±0,00, OG +3,00, eaves +5,90, ridge +8,50."""
import sys
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

W, H = landscape(A3)
S = 10 * mm          # 1:100 → 10 mm per metre
L, B, T = 0.365, 12.0, 9.0
EAVE, RIDGE, OG = 5.9, 8.5, 3.0


def frame(c, plan, nr):
    c.setStrokeColorRGB(0, 0, 0)
    c.setLineWidth(0.8)
    c.rect(20 * mm, 10 * mm, W - 30 * mm, H - 20 * mm)
    x0, y0, w, h = W - 10 * mm - 150 * mm, 10 * mm, 150 * mm, 45 * mm
    c.setLineWidth(0.5)
    c.rect(x0, y0, w, h)
    for k in (11, 22, 33):
        c.line(x0, y0 + k * mm, x0 + w, y0 + k * mm)
    c.line(x0 + 30 * mm, y0, x0 + 30 * mm, y0 + h)
    c.setFont("Helvetica", 6.5)
    rows = (("Bauherr", "Familie Muster"), ("Projekt", "Musterhaus, Neubau EFH"),
            ("Planinhalt", plan), ("Plan-Nr. / Datum", f"{nr} / 01.10.2026"))
    for i, (k, v) in enumerate(rows):
        yy = y0 + h - (i + 1) * 11 * mm + 4 * mm
        c.drawString(x0 + 2 * mm, yy, k)
        c.setFont("Helvetica-Bold", 7.5)
        c.drawString(x0 + 32 * mm, yy, v)
        c.setFont("Helvetica", 6.5)


def caption(c, x, y, text):
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x, y, text)


def plan(c, ox, oy, storey):
    P = lambda x, y: (ox + x * S, oy + y * S)
    # outer wall ring (grey fill)
    c.setFillColorRGB(0.55, 0.55, 0.55)
    c.setLineWidth(0.5)
    p = c.beginPath()
    p.moveTo(*P(0, 0)); p.lineTo(*P(B, 0)); p.lineTo(*P(B, T)); p.lineTo(*P(0, T)); p.close()
    p.moveTo(*P(L, L)); p.lineTo(*P(L, T - L)); p.lineTo(*P(B - L, T - L)); p.lineTo(*P(B - L, L)); p.close()
    c.drawPath(p, stroke=1, fill=1, fillMode=0)
    # inner walls
    w = 0.175
    for (x, y, dx, dy) in ((5.0, L, w, T - 2 * L), (L, 5.2, 5.0 - L, w),
                           (8.5, 5.2, B - L - 8.5, w), (8.5, 5.2, w, T - L - 5.2)):
        c.rect(*P(x, y), dx * S, dy * S, stroke=1, fill=1)
    c.setFillColorRGB(1, 1, 1)
    # windows (white openings with glass lines)
    c.setLineWidth(0.25)
    for (x, y, l, horiz) in ((1.5, 0, 1.51, True), (7.0, 0, 2.51, True), (10.2, 0, 1.01, True),
                             (1.5, T - L, 1.51, True), (6.0, T - L, 1.26, True), (9.5, T - L, 1.51, True),
                             (0, 2.0, 1.26, False), (B - L, 6.5, 1.26, False)):
        if horiz:
            c.rect(*P(x, y), l * S, L * S, stroke=1, fill=1)
            c.line(*P(x, y + L / 2), *P(x + l, y + L / 2))
        else:
            c.rect(*P(x, y), L * S, l * S, stroke=1, fill=1)
            c.line(*P(x + L / 2, y), *P(x + L / 2, y + l))
    # doors with swing arcs
    for (hx, hy, r, a0) in ((5.0 + w, 2.0, 0.885, 90), (2.0, 5.2, 0.76, 0), (9.5, 5.2 + w, 0.76, 0)):
        c.line(*P(hx, hy), *P(hx + (r if a0 == 0 else -r) * 0 + (r if a0 == 0 else 0), hy + (0 if a0 == 0 else 0)))
        c.arc(*P(hx - r, hy - r), *P(hx + r, hy + r), startAng=a0, extent=90)
    if storey == "EG":
        c.rect(*P(5.6, 0), 1.01 * S, L * S, stroke=1, fill=1)          # front door
    # stairs
    c.setLineWidth(0.2)
    for i in range(13):
        x = 5.4 + i * 0.26
        c.line(*P(x, 5.6), *P(x, 6.6))
    c.rect(*P(5.4, 5.6), 3.12 * S, 1.0 * S)
    c.line(*P(5.4, 6.1), *P(8.4, 6.1))
    # furniture-ish: table + chairs (circles)
    c.circle(*P(2.5, 7.0), 0.6 * S)
    # dimension chains
    c.setLineWidth(0.18)
    for y, pts in ((-1.0, (0, B)), (-0.6, (0, 1.5, 3.01, 7.0, 9.51, 10.2, 11.21, B))):
        c.line(*P(pts[0], y), *P(pts[-1], y))
        for x in pts:
            c.line(*P(x - 0.1, y - 0.1), *P(x + 0.1, y + 0.1))
            c.line(*P(x, y - 0.25), *P(x, y + 0.25))
    c.line(*P(-1.0, 0), *P(-1.0, T))
    for yv in (0, T):
        c.line(*P(-1.1, yv - 0.1), *P(-0.9, yv + 0.1))
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 7)
    c.drawString(*P(5.6, -0.9), "12,00")
    # section line A-A
    c.setLineWidth(0.6)
    c.setDash(6, 3)
    c.line(*P(7.6, -1.6), *P(7.6, T + 1.6))
    c.setDash()
    c.setFont("Helvetica-Bold", 9)
    c.drawString(*P(7.75, -1.8), "A"); c.drawString(*P(7.75, T + 1.5), "A")
    # level marker
    c.setFont("Helvetica", 7.5)
    c.drawString(*P(3.0, 3.0), "±0,00" if storey == "EG" else "+3,00")


def markers(c, ox, oy, levels):
    """Storey markers left of a view: «±0,00» over «0 EG»."""
    c.setLineWidth(0.3)
    for z, lab, val in levels:
        x, y = ox - 2.6 * S, oy + z * S
        c.line(x, y, x + 2.2 * S, y)
        c.setFont("Helvetica", 7)
        c.drawString(x, y + 4.5, val)
        if lab:
            c.drawString(x, y - 9, lab)


LEVELS = ((0.0, "0 EG", "±0,00"), (OG, "1 OG", "+3,00"),
          (EAVE, "", "+5,90"), (RIDGE, "", "+8,50"))


def eave_view(c, ox, oy, mirror=False, door=False):
    """Long side (12 m): wall to the eaves, roof seen as a band."""
    P = lambda x, y: (ox + x * S, oy + y * S)
    c.setLineWidth(0.5)
    c.line(*P(-1.5, 0), *P(B + 1.5, 0))                       # ground
    c.rect(*P(0, 0), B * S, EAVE * S)
    c.setFillColorRGB(0.35, 0.35, 0.35)
    p = c.beginPath()
    p.moveTo(*P(-0.5, EAVE - 0.2)); p.lineTo(*P(B + 0.5, EAVE - 0.2))
    p.lineTo(*P(B + 0.5, RIDGE)); p.lineTo(*P(-0.5, RIDGE)); p.close()
    c.drawPath(p, stroke=1, fill=1)
    c.setFillColorRGB(1, 1, 1)
    xs = (1.5, 7.0, 10.2) if not mirror else (1.5, 6.0, 9.5)
    ls = (1.51, 2.51, 1.01) if not mirror else (1.51, 1.26, 1.51)
    for x, l in zip(xs, ls):
        for z in (1.0, 1.0 + OG):
            xx = B - x - l if mirror else x
            c.rect(*P(xx, z), l * S, 1.4 * S, stroke=1, fill=0)
            c.line(*P(xx + l / 2, z), *P(xx + l / 2, z + 1.4))
    if door:
        c.rect(*P(5.6, 0), 1.01 * S, 2.2 * S)
    c.line(*P(-0.2, 0.3), *P(B + 0.2, 0.3))                   # plinth
    c.setFillColorRGB(0, 0, 0)


def gable_view(c, ox, oy):
    """Short side (9 m) with the gable."""
    P = lambda x, y: (ox + x * S, oy + y * S)
    c.setLineWidth(0.5)
    c.line(*P(-1.5, 0), *P(T + 1.5, 0))
    p = c.beginPath()
    p.moveTo(*P(0, 0)); p.lineTo(*P(T, 0)); p.lineTo(*P(T, EAVE))
    p.lineTo(*P(T / 2, RIDGE)); p.lineTo(*P(0, EAVE)); p.close()
    c.drawPath(p, stroke=1, fill=0)
    c.line(*P(-0.6, EAVE - 0.4), *P(T / 2, RIDGE + 0.25)); c.line(*P(T / 2, RIDGE + 0.25), *P(T + 0.6, EAVE - 0.4))
    for x in (1.5, 6.3):
        for z in (1.0, 1.0 + OG):
            c.rect(*P(x, z), 1.26 * S, 1.4 * S)
    c.rect(*P(T / 2 - 0.5, 6.4), 1.0 * S, 1.0 * S)
    c.line(*P(-0.2, 0.3), *P(T + 0.2, 0.3))


def section(c, ox, oy):
    P = lambda x, y: (ox + x * S, oy + y * S)
    c.setLineWidth(0.5)
    c.line(*P(-1.5, -0.1), *P(T + 1.5, -0.1))
    c.setFillColorRGB(0.4, 0.4, 0.4)
    for z, d in ((-0.3, 0.3), (OG - 0.25, 0.25)):
        c.rect(*P(0, z), T * S, d * S, stroke=1, fill=1)
    for x in (0, T - L):
        c.rect(*P(x, -0.3), L * S, (EAVE + 0.3) * S, stroke=1, fill=1)
    c.rect(*P(5.2, 0), 0.175 * S, (OG - 0.25) * S, stroke=1, fill=1)
    c.rect(*P(5.2, OG), 0.175 * S, (EAVE - OG) * S, stroke=1, fill=1)
    p = c.beginPath()
    p.moveTo(*P(-0.6, EAVE - 0.4)); p.lineTo(*P(T / 2, RIDGE)); p.lineTo(*P(T + 0.6, EAVE - 0.4))
    p.lineTo(*P(T + 0.6, EAVE - 0.65)); p.lineTo(*P(T / 2, RIDGE - 0.3)); p.lineTo(*P(-0.6, EAVE - 0.65)); p.close()
    c.drawPath(p, stroke=1, fill=1)
    c.setFillColorRGB(0, 0, 0)
    for i in range(16):                                      # stair in section
        c.line(*P(1.0 + i * 0.26, i * 0.1875), *P(1.26 + i * 0.26, i * 0.1875))


def main(out):
    c = canvas.Canvas(out, pagesize=(W, H))
    c.setTitle("Musterhaus Plansatz (Beispiel)")
    ox, oy = 50 * mm, 75 * mm
    for st, name, nr in (("EG", "Erdgeschoss", "01"), ("OG", "Obergeschoss", "02")):
        frame(c, f"Grundriss {name}", nr)
        plan(c, ox, oy, st)
        caption(c, ox, 35 * mm, f"{name}  M 1:100")
        c.showPage()
    frame(c, "Ansichten Nord / Süd", "03")
    eave_view(c, 70 * mm, 175 * mm, mirror=True); markers(c, 70 * mm, 175 * mm, LEVELS)
    eave_view(c, 70 * mm, 50 * mm, door=True); markers(c, 70 * mm, 50 * mm, LEVELS)
    caption(c, 70 * mm, 35 * mm, "Ansichten N S  M 1:100")
    c.showPage()
    frame(c, "Ansichten Ost / West", "04")
    gable_view(c, 70 * mm, 120 * mm); markers(c, 70 * mm, 120 * mm, LEVELS)
    gable_view(c, 220 * mm, 120 * mm)
    caption(c, 70 * mm, 35 * mm, "Ansichten O W  M 1:100")
    c.showPage()
    frame(c, "Schnitt A-A", "05")
    section(c, 90 * mm, 90 * mm); markers(c, 90 * mm, 90 * mm, LEVELS)
    caption(c, 90 * mm, 35 * mm, "Schnitt A-A  M 1:100")
    c.showPage()
    c.save()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "musterhaus_plansatz_1-100_A3.pdf")
