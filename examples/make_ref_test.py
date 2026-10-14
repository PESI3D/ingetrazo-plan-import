"""3 sheets 1:100 A3: EG + OG of a 10 x 8 m house at different paper
positions, and «Ansicht Nord» (10 m wide, 6 m high). Reference corner =
the house's south-west corner (on the north elevation: bottom right)."""
import sys
from reportlab.lib.pagesizes import A3, landscape
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
W, H = landscape(A3)
c = canvas.Canvas(sys.argv[1], pagesize=(W, H))
for name, ox, oy in (("Erdgeschoss", 60, 80), ("1. Obergeschoss", 150, 40)):
    c.setLineWidth(0.5)
    c.rect(ox * mm, oy * mm, 100 * mm, 80 * mm)
    c.rect((ox + 3.65) * mm, (oy + 3.65) * mm, 92.7 * mm, 72.7 * mm)
    c.line((ox + 60) * mm, oy * mm, (ox + 60) * mm, (oy + 80) * mm)
    c.setFont("Helvetica", 9); c.drawString(20 * mm, 20 * mm, f"{name} M 1:100")
    c.showPage()
c.setLineWidth(0.5)
c.rect(40 * mm, 60 * mm, 100 * mm, 60 * mm)
c.line(40 * mm, 90 * mm, 140 * mm, 90 * mm)
c.setFont("Helvetica", 9); c.drawString(20 * mm, 20 * mm, "Ansicht Nord M 1:100")
c.showPage(); c.save()
