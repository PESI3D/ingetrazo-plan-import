# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Pesi (pesi3d.de)
"""Plan Import — floor plans from vector PDFs as clean linework, for
IngeTrazo.

A PDF page (or a whole plan set) becomes edges at its plan scale (1 : N):
one group per page, all pages of one import = one undo step. Lines
(strokes) and the outlines of filled shapes (walls drawn as grey fills)
become edges; Bezier curves become curve edges (an arc selects as one
contour). Text, images and shadings are skipped.

v0.2 — plan sets: a page list with the drawing name and scale read from
each sheet («Erdgeschoss 1:100»), editable; pages laid out side by side or
stacked on their elevations.

v0.3 — Live Preview like the other PESI3D plugins: the import shows in the
model as one undo step and follows every change; OK keeps it, Cancel takes
it back. Page geometry is read once (in the background) and every built
sheet is kept, so a change only rebuilds what it touches.

v0.4 — page view: the selected sheet as a picture beside the page list
(zoom/pan), drawn from its own paths and texts; texts that read like a
plan scale («1:100») are marked in red.

v0.4.1 — Floor height: elevations for stacked sheets filled in one click
(Base + n × Floor height over the ticked pages, in list order); switching
to Stacked fills them by itself while all are still 0.

v0.5 — storey levels from the sheets: the story markers CAD sections and
elevations print («-1 GG» over «-3,68») are matched to the plan names
(Gartengeschoss ↔ GG, 1. Obergeschoss ↔ 1. OG), else a plan's own level
marker («±0,00 = +571,30») is used. Shown in the Level column, used by
Fill elevations; the typical storey height becomes the Floor height.

v0.5.1 — what the sheets did not tell is marked: green = read from the
sheet, orange = suggested (scale: the set's usual scale; level: previous
storey + Floor height; name: «file pN»), please check; a warning line lists
the pages. Typing a value clears the mark.

v0.6 — Measure scale: pick the page in the list, click «Measure scale…»
under the page view, click the two ends of a known distance on the sheet
(snaps to line ends), type its real length — the page's scale is computed
(rounded to a usual scale when within 1.5 %) and marked blue.

v0.7 — sheets know what they show: a check routine classifies every page
(column Type: floor plan, site plan, elevation N/E/S/W, section — from the
name, else from the content: storey markers = a view). In Stacked the
floor plans stack on their elevations, the site plan lies flat, and
elevations/sections stand upright around the building at View distance,
facing outwards in the right direction, their ±0,00 line at z = 0.

v0.8 — crop frames: «Crop» draws the frame of the selected row on its
sheet (title block and frame stay out), «+ View» adds another row for the
same sheet with its own frame (two elevations or sections on one sheet
become two upright views, E and W suggested), «Clear crop» removes it.
Placement, building box and ±0,00 line use only what is inside a frame.

v0.9 — storeys to the «Levels» panel: with Stacked, the storeys read from
the sheets (and the ticked floor plans' elevations) are written into the
document data of the Niveles/Levels extension (key «niveles»), merged with
the levels already there — dashed guides in elevations, cursor snapping.
Part of the same undo step as the import.

v0.12 — reference point: per row a point clicked on the sheet (same
building corner / grid crossing on every sheet). Stacked: floor plans and
site plan are moved so it lands on the origin (all storeys one above the
other, whatever their place on the paper); elevations and sections stand
so that their reference point lies on the same vertical line, and
without a ±0,00 text it marks z = 0.

v0.11 — Auto crop: crop frames suggested from the sheet (autocrop.py) —
sheet frame, separator lines and title block (box in the lower right
corner, or title-block words) are left out, the remaining line clusters
are the drawings; several on one sheet give one row each. For the sheet
shown or all ticked sheets; marked orange to check.

v0.10 — layers: each ticked sheet goes onto a layer («Layers» tab) named
after its drawing name (no scale; «file pN» without one). Missing layers
are created, undo removes them again — same undo step as the import.

1.1 — Filter (small parts, colours, thin lines, hatches) and Clean up
(merge duplicates and overlaps, close gaps) in cleanup.py; a toolbar of
its own (PESI3D block).

1.0 — first release (v0.12 + fix: replaced/removed table rows no longer
leave their widgets behind as ghosts after a second file or Reset).

DXF/DWG: IngeTrazo's own import.

Install: copy the whole folder ``plan_import_tool`` into the plugins folder
(Extensions ▸ Open plugins folder; on Windows %APPDATA%\\ingetrazo\\plugins)
and restart IngeTrazo. Needs IngeTrazo ≥ 0.5 (extension API 2).

PDF parsing: pypdf (BSD-3-Clause), vendored in ``_vendor/pypdf``.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

KEY = "plan_import_tool"
TITLE = "Plan Import"
VERSION = "1.2.1"
SETTINGS_KEY = "plugins/plan_import_tool/params"
WHITE = 0.97          # r, g and b all above this = white (paper masks)
GAP = 0.1             # side by side: gap between sheets, × sheet width


def default_params() -> dict:
    return {"file": "", "scale": 100, "strokes": True, "fills": True,
            "skip_white": True, "layout": "side", "last_dir": "",
            "floor_h": 3.0, "base_z": 0.0, "view_dist": 5.0,
            "to_levels": True, "to_layers": True,
            "min_part_mm": 0.0, "min_width_mm": 0.0, "skip_hatch": False,
            "skip_colours": [], "dedupe": True, "gap_mm": 5.0}


#: Options the imported linework depends on (cache keys, preview state).
CONTENT_KEYS = ("strokes", "fills", "skip_white", "min_part_mm",
                "min_width_mm", "skip_hatch", "skip_colours", "dedupe",
                "gap_mm")


def content_opts(p) -> tuple:
    return tuple((k, tuple(sorted(p.get(k) or ())) if k == "skip_colours"
                  else p.get(k)) for k in CONTENT_KEYS)


def _load_params() -> dict:
    p = default_params()
    try:
        from PySide6.QtCore import QSettings
        raw = QSettings().value(SETTINGS_KEY, "")
        if raw:
            p.update({k: v for k, v in json.loads(raw).items() if k in p})
    except Exception:                                      # noqa: BLE001
        pass
    return p


def _save_params(p: dict) -> None:
    try:
        from PySide6.QtCore import QSettings
        QSettings().setValue(SETTINGS_KEY, json.dumps(p))
    except Exception:                                      # noqa: BLE001
        pass


def _pv():
    from . import pdf_vectors
    return pdf_vectors


# ---------------------------------------------------------------------------
# PDF → segments (pure Python, testable without Qt)
# ---------------------------------------------------------------------------

def _is_white(rgb) -> bool:
    return all(c >= WHITE for c in rgb)


def collect_runs(vectors, p: dict):
    """The runs to import, by the content and filter options. Returns
    ``(runs, stats)``; runs are ``(points_pt, curved)``."""
    from .cleanup import filter_paths
    runs = []
    paths, fcount = filter_paths(vectors.paths, p)
    stats = {"paths": 0, "white": 0, "fills": 0, "strokes": 0, **fcount}
    for path in paths:
        use = False
        if path.stroke and p["strokes"]:
            if p["skip_white"] and _is_white(path.stroke_rgb):
                stats["white"] += 1
            else:
                use = True
                stats["strokes"] += 1
        if not use and path.fill and p["fills"]:
            if p["skip_white"] and _is_white(path.fill_rgb):
                stats["white"] += 1
            else:
                use = True
                stats["fills"] += 1
        if use:
            stats["paths"] += 1
            runs.extend(path.runs)
    return runs, stats


#: Coordinates in the style record: integer thousandths of a sheet point.
STYLE_GRID = 1000


def style_key(path) -> tuple:
    """How a path is drawn: ``(stroke width mm | 0, stroke rgb | None,
    fill rgb | None)`` — colours to two decimals."""
    pen = path.stroke and not _is_white(path.stroke_rgb)
    width = round(path.width * 25.4 / 72.0, 2) if pen else 0.0
    srgb = tuple(round(c, 2) for c in path.stroke_rgb) if pen else None
    frgb = (tuple(round(c, 2) for c in path.fill_rgb)
            if path.fill and not _is_white(path.fill_rgb) else None)
    return width, srgb, frgb


def style_record(vectors, p: dict, crop=None) -> dict:
    """The sheet's drawing kept BY LINE STYLE, for plugins that tell
    building parts apart by how they are drawn (Walls: the cut walls are
    the filled shapes or the heavy pen). ``{"grid", "styles": [{"w",
    "stroke", "fill", "len", "area"}], "runs": {sid: [[x, y, x, y, …]]},
    "fills": [{"s": sid, "eo": evenodd, "rings": [[x, y, …]]}]}`` in sheet
    points × ``STYLE_GRID``, cut to ``crop``. Same paths as the import
    (filters and «Skip white» applied)."""
    from .cleanup import filter_paths
    paths, _counts = filter_paths(vectors.paths, p)
    g = STYLE_GRID
    styles, index = [], {}
    runs_by, fills = {}, []

    def sid_of(path):
        k = style_key(path)
        if k not in index:
            index[k] = len(styles)
            styles.append({"w": k[0], "stroke": list(k[1]) if k[1] else None,
                           "fill": list(k[2]) if k[2] else None,
                           "len": 0.0, "area": 0.0})
        return index[k]

    for path in paths:
        k = style_key(path)
        if k[1] is None and k[2] is None:
            continue                         # white on white: not drawn
        sid = sid_of(path)
        st = styles[sid]
        for run, _curved in clip_runs(path.runs, crop):
            if len(run) < 2:
                continue
            runs_by.setdefault(sid, []).append(
                [int(round(c * g)) for pt in run for c in pt])
            st["len"] += sum(math.hypot(b[0] - a[0], b[1] - a[1])
                             for a, b in zip(run, run[1:]))
        if k[2] is not None:
            rings = []
            for run, _curved in path.runs:
                if len(run) < 3:
                    continue
                if crop is not None:
                    xs = [x for x, _y in run]
                    ys = [y for _x, y in run]
                    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
                    if not (crop[0] <= cx <= crop[2] and crop[1] <= cy <= crop[3]):
                        continue
                rings.append([int(round(c * g)) for pt in run for c in pt])
                a = 0.0
                for (x0, y0), (x1, y1) in zip(run, run[1:] + run[:1]):
                    a += x0 * y1 - x1 * y0
                st["area"] += abs(a) / 2.0
            if rings:
                fills.append({"s": sid, "eo": bool(path.evenodd), "rings": rings})
    for st in styles:
        st["len"] = round(st["len"], 1)
        st["area"] = round(st["area"], 1)
    # The geometry packed (zlib + base64 JSON): a sheet holds tens of
    # thousands of lines and travels in the document.
    import base64
    import zlib
    blob = json.dumps({"runs": {str(k): v for k, v in runs_by.items()},
                       "fills": fills}, separators=(",", ":"))
    data = base64.b64encode(zlib.compress(blob.encode("ascii"), 6)).decode("ascii")
    return {"grid": g, "styles": styles, "data": data}


def unpack_style_record(rec) -> dict:
    """``{"runs": {sid: [[x, y, …]]}, "fills": [...]}`` of a style record."""
    import base64
    import zlib
    return json.loads(zlib.decompress(base64.b64decode(rec["data"])))


def clean_runs(runs, p: dict, mpp: float):
    """Clean up by the options: merge duplicates/overlaps, close gaps
    (``gap_mm`` real millimetres). Returns ``(runs, stats)``."""
    from .cleanup import close_gaps, dedupe_overlaps
    st = {"merged": 0, "closed": 0}
    if p.get("dedupe", True) and runs:
        runs, st["merged"] = dedupe_overlaps(runs)
    gap = float(p.get("gap_mm", 0.0) or 0.0)
    if gap > 0 and runs and mpp > 0:
        runs, st["closed"] = close_gaps(runs, gap / 1000.0 / mpp)
    return runs, st


def metres_per_point(scale_n: float) -> float:
    """Real metres per page point at a plan scale of 1 : ``scale_n``."""
    return 25.4 / 72.0 / 1000.0 * float(scale_n)


FLAT = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0))     # sheet x, y → world


def build_group(runs, mpp: float, name: str, offset=(0.0, 0.0, 0.0),
                axes=FLAT):
    """The runs as ONE classic Group of edges (metres): a sheet point
    (x, y) lands at ``offset + x·mpp·U + y·mpp·V`` with ``axes = (U, V)``
    — flat on the ground by default, upright for elevations/sections.
    Identical edges are merged by the welding; curved runs get one curve
    id each."""
    import numpy as np
    from PySide6.QtGui import QMatrix4x4
    from core.group import Group
    from core.mesh import Mesh

    o = np.asarray(offset, dtype=np.float64)
    u = np.asarray(axes[0], dtype=np.float64) * mpp
    v = np.asarray(axes[1], dtype=np.float64) * mpp
    mesh = Mesh()
    xy, ia, ib, flags = [], [], [], []
    for run, curved in runs:
        if len(run) < 2:
            continue
        cid = Mesh.next_curve_id() if curved and len(run) > 2 else None
        base = len(xy)
        xy.extend(run)
        for k in range(len(run) - 1):
            ia.append(base + k)
            ib.append(base + k + 1)
            flags.append((False, cid, None))
    if not xy:
        raise ValueError("Nothing to import with these options.")
    a = np.asarray(xy, dtype=np.float64)
    pts = o + np.outer(a[:, 0], u) + np.outer(a[:, 1], v)
    vobjs, ids = mesh.bulk_weld(pts)
    ids = np.asarray(ids)
    mesh.add_edges_welded(vobjs, ids[ia], ids[ib], flags=flags)
    if not mesh.edges:
        raise ValueError("Nothing to import with these options.")
    group = Group(mesh, name=name)
    # Axes stated explicitly: a group without axes gets a frame DERIVED
    # from its points (tightest footprint), which on a sheet with a plan
    # header comes out turned. A plan lies on the world axes; an upright
    # view gets the sheet's own (red along the drawing, blue up).
    U = np.asarray(axes[0], dtype=float)
    V = np.asarray(axes[1], dtype=float)
    W = np.cross(U, V)
    group.axes = QMatrix4x4(U[0], V[0], W[0], o[0],
                            U[1], V[1], W[1], o[1],
                            U[2], V[2], W[2], o[2],
                            0.0, 0.0, 0.0, 1.0)
    return group


def clip_runs(runs, rect):
    """The runs cut to ``rect`` = (x0, y0, x1, y1) in sheet points —
    segments crossing the frame are cut at it (Liang–Barsky)."""
    if rect is None:
        return runs
    x0, y0, x1, y1 = rect
    out = []
    for run, curved in runs:
        cur = None
        for (ax, ay), (bx, by) in zip(run, run[1:]):
            dx, dy = bx - ax, by - ay
            t0, t1 = 0.0, 1.0
            ok = True
            for pp, qq in ((-dx, ax - x0), (dx, x1 - ax),
                           (-dy, ay - y0), (dy, y1 - ay)):
                if pp == 0:
                    if qq < 0:
                        ok = False
                        break
                    continue
                t = qq / pp
                if pp < 0:
                    if t > t1:
                        ok = False
                        break
                    t0 = max(t0, t)
                else:
                    if t < t0:
                        ok = False
                        break
                    t1 = min(t1, t)
            if not ok or t1 - t0 <= 1e-12:
                if cur and len(cur) > 1:
                    out.append((cur, curved))
                cur = None
                continue
            p0 = (ax + t0 * dx, ay + t0 * dy)
            p1 = (ax + t1 * dx, ay + t1 * dy)
            if cur is None or t0 > 0.0:
                if cur and len(cur) > 1:
                    out.append((cur, curved))
                cur = [p0]
            cur.append(p1)
            if t1 < 1.0:
                out.append((cur, curved))
                cur = None
        if cur and len(cur) > 1:
            out.append((cur, curved))
    return out


def runs_bbox(runs):
    """``(x0, y0, x1, y1)`` of the runs in sheet points, or None."""
    xs = [x for run, _c in runs for x, _y in run]
    ys = [y for run, _c in runs for _x, y in run]
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


# Type of a sheet (column «Type») and where it goes in the Stacked model
TYPES = ("Floor plan", "Site plan", "Elevation N", "Elevation E",
         "Elevation S", "Elevation W", "Section", "Flat (other)")
UPRIGHT = {"Elevation N": "N", "Elevation E": "E", "Elevation S": "S",
           "Elevation W": "W", "Section": "S"}


def type_from_class(kind, sides):
    if kind == "elev":
        return f"Elevation {sides[0]}" if sides else "Elevation S"
    return {"plan": "Floor plan", "site": "Site plan",
            "section": "Section"}.get(kind, "Floor plan")


def upright_placement(side, slot, box, view_bbox_m, datum_m, base_z,
                      distance, ref_m=None):
    """``(offset, (U, V))`` for an upright view on ``side`` of the
    building ``box`` (x0, y0, x1, y1 in m), ``slot`` = how many views
    stand on that side already (each further out by ``distance``).

    The drawing's horizontal centre goes to the middle of that facade;
    its ±0,00 line (``datum_m``, sheet metres) to z = 0, else its lowest
    point to ``base_z``. Seen from outside, the drawing reads like on the
    sheet: Nord = looking south (sheet right = west) and so on.

    ``ref_m`` (sheet metres): the reference point — the same building
    corner as on the floor plans, which sit with theirs on the origin.
    It goes onto the vertical line through the origin (instead of the
    facade middle); without a ±0,00 line its height is z = 0."""
    x0, y0, x1, y1 = box
    bx0, by0, bx1, _by1 = view_bbox_m
    cx = (bx0 + bx1) / 2.0
    z0 = -datum_m if datum_m is not None else base_z - by0
    off = distance * (slot + 1)
    mx, my = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    up = (0.0, 0.0, 1.0)
    if ref_m is not None:
        if datum_m is None:
            z0 = -ref_m[1]
        u = ref_m[0]
        if side == "N":
            return (u, y1 + off, z0), ((-1.0, 0.0, 0.0), up)
        if side == "E":
            return (x1 + off, -u, z0), ((0.0, 1.0, 0.0), up)
        if side == "W":
            return (x0 - off, u, z0), ((0.0, -1.0, 0.0), up)
        return (-u, y0 - off, z0), ((1.0, 0.0, 0.0), up)        # S
    if side == "N":
        return (mx + cx, y1 + off, z0), ((-1.0, 0.0, 0.0), up)
    if side == "E":
        return (x1 + off, my - cx, z0), ((0.0, 1.0, 0.0), up)
    if side == "W":
        return (x0 - off, my + cx, z0), ((0.0, -1.0, 0.0), up)
    return (mx - cx, y0 - off, z0), ((1.0, 0.0, 0.0), up)       # S


def layout_offsets(sheets, layout: str) -> list:
    """Where each sheet's lower-left lands (metres). ``sheets`` =
    ``[(width_m, elevation_m), ...]`` for EVERY page of the file, ticked or
    not — so ticking a page on or off never moves the others (the preview
    keeps what it already built). ``side``: in a row along red with a gap;
    ``stack``: all at the origin, Z = elevation."""
    out = []
    x = 0.0
    for w, z in sheets:
        if layout == "stack":
            out.append((0.0, 0.0, float(z)))
        else:
            out.append((x, 0.0, 0.0))
            x += w * (1.0 + GAP)
    return out


def sheet_name(path, page_no: int, name: str | None, scale_n: int) -> str:
    base = name.strip() if name and name.strip() else \
        f"{Path(path).stem} p{page_no}"
    return f"{base} 1:{int(scale_n)}"


LEVELS_KEY = "niveles"      # the Niveles/Levels extension's document data


def merge_levels(old, new):
    """Levels data for the Niveles extension: ``old`` (its document data or
    None) plus ``new`` = [(name, z)]; a level at the same height (±1 cm)
    or with the same name is replaced, the rest kept."""
    data = dict(old) if isinstance(old, dict) else {}
    levels = [dict(lv) for lv in data.get("levels", [])
              if isinstance(lv, dict) and "name" in lv and "z" in lv]
    for name, z in new:
        levels = [lv for lv in levels
                  if abs(float(lv["z"]) - z) > 0.01 and lv["name"] != name]
        levels.append({"name": name, "z": round(float(z), 3)})
    levels.sort(key=lambda lv: float(lv["z"]))
    data["levels"] = levels
    data.setdefault("guides", True)
    return data


def layer_name(path, page_no: int, name: str | None) -> str:
    """The layer a sheet goes on: the drawing name (no scale), else
    «file pN»."""
    return name.strip() if name and name.strip() else \
        f"{Path(path).stem} p{page_no}"


def layer_commands(groups, names):
    """One AssignLayerCommand per group: creates the layer when missing
    (and removes it again on undo), puts the group on it."""
    from core.history import AssignLayerCommand
    return [AssignLayerCommand([g], n) for g, n in zip(groups, names) if n]


def insert_groups(viewport, groups, select=True, extra=()):
    """All groups as ONE undo step. ``select``: selected afterwards (the
    Live Preview inserts unselected — no boxes over the drawing).
    ``extra``: further commands in the same step (levels). Returns the
    command."""
    from core.history import CompoundCommand, InsertGroupCommand

    class _InsertAll(CompoundCommand):
        def do(self, scene):
            super().do(scene)
            scene.selection.clear()
            if self.select:
                for g in self.groups:
                    scene.selection.add(g)
            scene.version += 1

    cmd = _InsertAll([InsertGroupCommand(g) for g in groups] + list(extra))
    cmd.select = select
    cmd.groups = list(groups)
    hist = viewport.history
    hist.execute(cmd)
    err = getattr(hist, "last_error", None)
    if err:
        raise RuntimeError(err)
    notify = getattr(viewport, "notify_scene_changed", None)
    if notify:
        notify()
    viewport.update()
    return cmd


# ---------------------------------------------------------------------------
# The dialog
# ---------------------------------------------------------------------------


def _make_dialog_class():
    import threading
    import time

    from PySide6.QtCore import QEventLoop, QObject, Qt, QTimer, Signal
    from PySide6.QtWidgets import (
        QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QDialog,
        QDoubleSpinBox, QFileDialog, QFormLayout, QHBoxLayout, QHeaderView,
        QInputDialog, QLabel, QLineEdit, QMessageBox, QProgressDialog,
        QPushButton, QToolButton, QMenu,
        QComboBox, QRadioButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
        QVBoxLayout, QWidget)
    from .page_view import make_page_view_class, page_points, render_page
    PageView = make_page_view_class()

    (COL_USE, COL_PAGE, COL_NAME, COL_TYPE, COL_SCALE, COL_ELEV, COL_LEVEL,
     COL_PAPER, COL_REF) = range(9)

    def row_widget(*items, stretch=None):
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        for i, it in enumerate(items):
            row.addWidget(it, 1 if i == stretch else 0)
        if stretch is None:
            row.addStretch(1)
        w = QWidget()
        w.setLayout(row)
        return w

    class _Relay(QObject):
        # Signal(object) on a queued connection to a bound method: the
        # worker never touches widgets (docs/plugins.md threading rule).
        msg = Signal(object)

    class PlanImportDialog(QDialog):
        _PV_ON = ("QPushButton { background: #2e9e4f; color: white; "
                  "font-weight: bold; border: 1px solid #1f7a3a; "
                  "border-radius: 3px; padding: 3px 8px; }")
        _BANNER = {
            "live": "background: #1f5f33; color: #e8ffe9;",
            "busy": "background: #7a5a12; color: #fff6e0;",
            "paused": "background: #5c4a1a; color: #ffe9b8;",
            "error": "background: #7a1f1f; color: #ffecec;",
        }

        def __init__(self, viewport, parent=None):
            super().__init__(parent)
            self.viewport = viewport
            self.setWindowTitle(f"{TITLE} {VERSION}")
            self.setModal(False)
            self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
            self.resize(1240, 720)
            self.p = _load_params()
            self._pages = []             # [(w_pt, h_pt)]
            self._titles = {}            # page index -> (name, scale)
            self._vec = {}               # page index -> PageVectors
            self._built = {}             # build key -> Group
            self._texts = {}             # page index -> [PageText]
            self._images = {}            # page index -> QImage (few)
            self._shown = None           # page index in the page view
            self._rowpage = []           # table row -> page index
            self._crop = {}              # table row -> crop rect (sheet pt)
            self._ref = {}               # table row -> reference pt (sheet)
            self._levels = {}            # page index -> (level_m, source)
            self._auto_elev = True       # elevations not typed by hand yet
            self._mark = {}              # (row, col) -> "sheet"|"suggested"
            self._stories = []           # [(label, level_m)]
            self._scan_id = 0
            self._busy = False           # filling widgets: no _changed
            self._computing = False
            self._preview_cmd = None
            self._timer = QTimer(self)
            self._timer.setSingleShot(True)
            self._timer.setInterval(400)
            self._timer.timeout.connect(self._refresh_preview)
            self._relay = _Relay()
            self._relay.msg.connect(self._on_worker,
                                    Qt.ConnectionType.QueuedConnection)
            self._build_ui()
            f = self.p.get("file") or ""
            if f and Path(f).is_file():
                self._open(f)

        # ---- UI ----------------------------------------------------------
        def _build_ui(self):
            lay = QVBoxLayout(self)
            form = QFormLayout()
            lay.addLayout(form)

            self.ed_file = QLineEdit(self)
            self.ed_file.setReadOnly(True)
            btn = QPushButton("Browse…", self)
            btn.clicked.connect(self._browse)
            form.addRow("File:", row_widget(self.ed_file, btn, stretch=0))

            split = QSplitter(Qt.Orientation.Horizontal, self)
            left = QWidget(split)
            ll = QVBoxLayout(left)
            ll.setContentsMargins(0, 0, 0, 0)
            self.table = QTableWidget(0, 9, left)
            self.table.setHorizontalHeaderLabels(
                ["", "Page", "Name", "Type", "Scale 1:", "Elevation",
                 "Level (sheet)", "Paper", "Ref point"])
            self.table.verticalHeader().setVisible(False)
            self.table.setSelectionMode(
                QAbstractItemView.SelectionMode.SingleSelection)
            self.table.setSelectionBehavior(
                QAbstractItemView.SelectionBehavior.SelectRows)
            self.table.currentCellChanged.connect(
                lambda r, _c, _pr, _pc: self._row_selected(r))
            hh = self.table.horizontalHeader()
            for c in (COL_USE, COL_PAGE, COL_TYPE, COL_SCALE, COL_ELEV,
                      COL_LEVEL,
                      COL_PAPER, COL_REF):
                hh.setSectionResizeMode(
                    c, QHeaderView.ResizeMode.ResizeToContents)
            hh.setSectionResizeMode(COL_NAME, QHeaderView.ResizeMode.Stretch)
            self.table.itemChanged.connect(self._item_changed)
            ll.addWidget(self.table, 1)

            b_all = QPushButton("All", self)
            b_none = QPushButton("None", self)
            b_all.clicked.connect(lambda: self._check_all(True))
            b_none.clicked.connect(lambda: self._check_all(False))
            self.lb_scan = QLabel("", self)
            ll.addWidget(row_widget(b_all, b_none, self.lb_scan, stretch=2))
            self.lb_warn = QLabel("", left)
            self.lb_warn.setWordWrap(True)
            self.lb_warn.setStyleSheet(
                "QLabel { background: #ffd9a8; color: #3a2400; "
                "padding: 4px 6px; border-radius: 3px; }")
            self.lb_warn.setVisible(False)
            ll.addWidget(self.lb_warn)
            right = QWidget(split)
            rl = QVBoxLayout(right)
            rl.setContentsMargins(0, 0, 0, 0)
            self.view = PageView(right)
            self.view.set_message("Choose a vector PDF with Browse….")
            rl.addWidget(self.view, 1)
            self.btn_measure = QPushButton("Measure scale…", right)
            self.btn_measure.setToolTip(
                "Set the scale of the page shown here from a known "
                "distance: click its two ends on the sheet (snaps to line "
                "ends), then type the real length.")
            self.btn_measure.setCheckable(True)
            self.btn_measure.clicked.connect(self._measure_clicked)
            self.btn_crop = QPushButton("Crop", right)
            self.btn_crop.setCheckable(True)
            self.btn_crop.setToolTip(
                "Drag a frame around the drawing of the selected row on its "
                "sheet — only what is inside is imported (title block, "
                "sheet frame and legends stay out).")
            self.btn_crop.clicked.connect(
                lambda on: self._crop_clicked(on, new_row=False))
            self.btn_view = QPushButton("+ View", right)
            self.btn_view.setCheckable(True)
            self.btn_view.setToolTip(
                "Another drawing on the same sheet (e.g. the second "
                "elevation): drag its frame — it becomes a row of its own.")
            self.btn_view.clicked.connect(
                lambda on: self._crop_clicked(on, new_row=True))
            self.btn_auto = QToolButton(right)
            self.btn_auto.setText("Auto crop")
            self.btn_auto.setPopupMode(
                QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            self.btn_auto.setToolTip(
                "Suggest the crop frames: sheet frame, separator lines and "
                "title block stay out, every drawing on the sheet gets a "
                "frame (two sections → two rows). Click = the sheet shown; "
                "the arrow: all ticked sheets. Suggested frames are marked "
                "orange in the Page column — check them in the page view.")
            menu = QMenu(self.btn_auto)
            menu.addAction("This sheet", lambda: self._auto_crop(False))
            menu.addAction("All ticked sheets",
                           lambda: self._auto_crop(True))
            self.btn_auto.setMenu(menu)
            self.btn_auto.clicked.connect(lambda: self._auto_crop(False))
            self.btn_clear = QPushButton("Clear crop", right)
            self.btn_clear.setToolTip(
                "Remove the frame of the selected row (an added view row is "
                "removed).")
            self.btn_clear.clicked.connect(self._clear_crop)
            self.btn_ref = QToolButton(right)
            self.btn_ref.setText("Ref point")
            self.btn_ref.setCheckable(True)
            self.btn_ref.setPopupMode(
                QToolButton.ToolButtonPopupMode.MenuButtonPopup)
            self.btn_ref.setToolTip(
                "Reference point of the selected row: click it on the sheet "
                "(snaps to line ends). Take the SAME point on every sheet — "
                "a building corner or a grid-line crossing. Stacked: the "
                "floor plans are moved so it lies on the origin (storeys "
                "exactly one above the other); elevations and sections "
                "stand so that their point is on the same vertical line — "
                "on an elevation without a ±0,00 text click the corner at "
                "±0,00 height, it becomes z = 0. Side by side ignores it.")
            rmenu = QMenu(self.btn_ref)
            rmenu.addAction("Clear reference point", self._clear_ref)
            rmenu.addAction("Clear all reference points",
                            lambda: self._clear_ref(all_rows=True))
            self.btn_ref.setMenu(rmenu)
            self.btn_ref.clicked.connect(self._ref_clicked)
            self.lb_measure = QLabel("", right)
            self.lb_measure.setWordWrap(True)
            rl.addWidget(row_widget(self.btn_crop, self.btn_view,
                                    self.btn_auto, self.btn_clear,
                                    self.btn_measure, self.btn_ref))
            rl.addWidget(self.lb_measure)
            split.addWidget(left)
            split.addWidget(right)
            split.setStretchFactor(0, 5)
            split.setStretchFactor(1, 6)
            split.setSizes([620, 600])
            lay.addWidget(split, 1)

            form2 = QFormLayout()
            lay.addLayout(form2)
            self.rb_side = QRadioButton("Side by side", self)
            self.rb_stack = QRadioButton("Stacked (on Elevation)", self)
            grp = QButtonGroup(self)
            grp.addButton(self.rb_side)
            grp.addButton(self.rb_stack)
            self.sp_floor = QDoubleSpinBox(self)
            self.sp_floor.setRange(0.0, 100.0)
            self.sp_floor.setDecimals(2)
            self.sp_floor.setSingleStep(0.25)
            self.sp_floor.setSuffix(" m")
            self.sp_floor.setValue(float(self.p.get("floor_h", 3.0)))
            self.sp_floor.setToolTip("Default storey height for Stacked.")
            self.sp_base = QDoubleSpinBox(self)
            self.sp_base.setRange(-10_000.0, 10_000.0)
            self.sp_base.setDecimals(2)
            self.sp_base.setSuffix(" m")
            self.sp_base.setValue(float(self.p.get("base_z", 0.0)))
            self.sp_base.setToolTip("Elevation of the first ticked page "
                                    "(e.g. -3.00 m for a basement).")
            self.btn_fill = QPushButton("Fill elevations", self)
            self.btn_fill.setToolTip(
                "Ticked pages in list order: the level read from the sheets "
                "(column Level) where there is one, else the previous page "
                "+ Floor height (the first: Base). Edit single rows "
                "afterwards.")
            self.btn_fill.clicked.connect(self._fill_elevations)
            self.sp_dist = QDoubleSpinBox(self)
            self.sp_dist.setRange(0.0, 1000.0)
            self.sp_dist.setDecimals(2)
            self.sp_dist.setSuffix(" m")
            self.sp_dist.setValue(float(self.p.get("view_dist", 5.0)))
            self.sp_dist.setToolTip(
                "Stacked: elevations and sections stand upright this far "
                "outside the floor plans (a second view on the same side "
                "stands twice as far).")
            self.sp_dist.valueChanged.connect(self._changed)
            form2.addRow("Layout:", row_widget(
                self.rb_side, self.rb_stack, QLabel("   Floor height:", self),
                self.sp_floor, QLabel("Base:", self), self.sp_base,
                self.btn_fill, QLabel("   View distance:", self),
                self.sp_dist))
            self.rb_stack.toggled.connect(self._stack_toggled)

            self.cb_strokes = QCheckBox("Lines (strokes)", self)
            self.cb_fills = QCheckBox("Filled shapes (outlines)", self)
            self.cb_white = QCheckBox("Skip white", self)
            form2.addRow("Content:", row_widget(
                self.cb_strokes, self.cb_fills, self.cb_white))

            def mm_spin(val, tip, step=0.1, dec=2, top=50.0):
                sp = QDoubleSpinBox(self)
                sp.setRange(0.0, top)
                sp.setDecimals(dec)
                sp.setSingleStep(step)
                sp.setSuffix(" mm")
                sp.setSpecialValueText("off")
                sp.setValue(float(val or 0.0))
                sp.setToolTip(tip)
                sp.valueChanged.connect(self._changed)
                return sp

            self.sp_small = mm_spin(
                self.p.get("min_part_mm", 0.0),
                "Leave out every drawn part smaller than this ON PAPER — "
                "letters turned into curves, dimension ticks, dots, small "
                "symbols. 0 = off.", 0.5, 1)
            self.sp_thin = mm_spin(
                self.p.get("min_width_mm", 0.0),
                "Leave out stroked lines thinner than this line width on "
                "paper (hairlines of dimensions, hatches, auxiliary lines). "
                "0 = off.", 0.05, 2, 5.0)
            self.cb_hatch = QCheckBox("Hatches", self)
            self.cb_hatch.setChecked(bool(self.p.get("skip_hatch", False)))
            self.cb_hatch.setToolTip(
                "Leave out hatch lines: straight lines of one colour and "
                "width lying in rows of five or more parallels with equal "
                "spacing (up to 2.2 mm on paper) — the way CAD programs "
                "print a hatch line by line. Stair treads are wider apart "
                "and stay; check the preview.")
            self.cb_hatch.toggled.connect(self._changed)
            self.btn_colours = QPushButton("Colours…", self)
            self.btn_colours.setToolTip(
                "The colours used on the ticked sheets — untick a colour to "
                "leave out everything drawn in it (e.g. a red layer of "
                "dimensions or furniture).")
            self.btn_colours.clicked.connect(self._colours_dialog)
            form2.addRow("Filter:", row_widget(
                QLabel("Small parts below", self), self.sp_small,
                QLabel("   Lines thinner than", self), self.sp_thin,
                QLabel("  ", self), self.cb_hatch, self.btn_colours))

            self.cb_dedupe = QCheckBox("Merge duplicates and overlaps", self)
            self.cb_dedupe.setChecked(bool(self.p.get("dedupe", True)))
            self.cb_dedupe.setToolTip(
                "Lines lying on each other become one (a wall outline drawn "
                "as fill AND stroke, double lines, overlapping pieces). "
                "Every end point stays, so junctions keep their vertices.")
            self.cb_dedupe.toggled.connect(self._changed)
            self.sp_gap = QDoubleSpinBox(self)
            self.sp_gap.setRange(0.0, 500.0)
            self.sp_gap.setDecimals(0)
            self.sp_gap.setSingleStep(5.0)
            self.sp_gap.setSuffix(" mm")
            self.sp_gap.setSpecialValueText("off")
            self.sp_gap.setValue(float(self.p.get("gap_mm", 5.0) or 0.0))
            self.sp_gap.setToolTip(
                "Loose line ends closer than this (REAL millimetres, at the "
                "row's scale) are joined to the nearest point — or onto the "
                "nearest line — so outlines close and walls meet. 0 = off.")
            self.sp_gap.valueChanged.connect(self._changed)
            form2.addRow("Clean up:", row_widget(
                self.cb_dedupe, QLabel("   Close gaps up to", self),
                self.sp_gap))
            self.cb_levels = QCheckBox(
                "Write storeys to the Levels panel (Stacked)", self)
            self.cb_levels.setChecked(bool(self.p.get("to_levels", True)))
            self.cb_levels.setToolTip(
                "The storeys read from the sheets (UG, GG, EG, 1. OG …) and "
                "the elevations of the ticked floor plans go into the "
                "«Levels» tab (Niveles extension): dashed level guides in "
                "elevations/sections, the cursor snaps to them. Merged with "
                "the levels already there; same undo step as the import.")
            self.cb_levels.toggled.connect(self._changed)
            form2.addRow("Levels:", row_widget(self.cb_levels))
            self.cb_layers = QCheckBox(
                "Put each sheet on its own layer, named after the plan", self)
            self.cb_layers.setChecked(bool(self.p.get("to_layers", True)))
            self.cb_layers.setToolTip(
                "Every ticked sheet goes onto a layer («Layers» tab) named "
                "after its drawing name — e.g. «Erdgeschoss», «Ansichten O W "
                "(Elevation W)». Missing layers are created; sheets with the "
                "same name share one. Same undo step as the import: undo "
                "removes the layers it created again.")
            self.cb_layers.toggled.connect(self._changed)
            form2.addRow("Layers:", row_widget(self.cb_layers))
            self._options_into_ui()
            for w in (self.rb_side, self.rb_stack, self.cb_strokes,
                      self.cb_fills, self.cb_white):
                w.toggled.connect(self._changed)

            self.lb_info = QLabel(
                "Choose a vector PDF (a CAD plot, not a scan). A plan set "
                "imports one group per page.", self)
            self.lb_info.setWordWrap(True)
            lay.addWidget(self.lb_info)

            self.pv_lbl = QLabel(self)
            self.pv_lbl.setWordWrap(True)
            self.pv_lbl.setVisible(False)
            lay.addWidget(self.pv_lbl)

            row = QHBoxLayout()
            self.preview = QPushButton("Preview", self)
            self.preview.setCheckable(True)
            self.preview.toggled.connect(self._preview_toggled)
            reset = QPushButton("Reset", self)
            reset.setToolTip("Back to the default settings and to the names "
                             "and scales read from the sheets.")
            reset.clicked.connect(self._reset)
            self.ok_btn = QPushButton("OK", self)
            self.ok_btn.setDefault(True)
            self.ok_btn.clicked.connect(self.accept)
            cancel = QPushButton("Cancel", self)
            cancel.clicked.connect(self.reject)
            row.addWidget(self.preview)
            row.addWidget(reset)
            row.addStretch(1)
            row.addWidget(self.ok_btn)
            row.addWidget(cancel)
            lay.addLayout(row)
            self._style_preview(False)
            self.ok_btn.setEnabled(False)
            self.preview.setEnabled(False)

        def _options_into_ui(self):
            self._busy = True
            (self.rb_stack if self.p["layout"] == "stack"
             else self.rb_side).setChecked(True)
            self.cb_strokes.setChecked(bool(self.p["strokes"]))
            self.cb_fills.setChecked(bool(self.p["fills"]))
            self.cb_white.setChecked(bool(self.p["skip_white"]))
            for w, k in ((self.sp_small, "min_part_mm"),
                         (self.sp_thin, "min_width_mm"),
                         (self.sp_gap, "gap_mm")):
                w.setValue(float(self.p.get(k) or 0.0))
            self.cb_hatch.setChecked(bool(self.p.get("skip_hatch")))
            self.cb_dedupe.setChecked(bool(self.p.get("dedupe", True)))
            self._sync_colour_btn()
            self._busy = False

        def _banner(self, kind, text):
            self.pv_lbl.setStyleSheet(
                self._BANNER[kind] + " padding: 6px 8px; border-radius: 3px;")
            self.pv_lbl.setText(text)
            self.pv_lbl.setVisible(True)

        def _style_preview(self, on):
            if on:
                self.preview.setText("● Live Preview ON")
                self.preview.setStyleSheet(self._PV_ON)
                self.preview.setToolTip("The preview follows every change by "
                                        "itself — click to switch it off.")
            else:
                self.preview.setText("Preview")
                self.preview.setStyleSheet("")
                self.preview.setToolTip("Show the import in the model; it "
                                        "then updates by itself on every "
                                        "change.")
                self.pv_lbl.setVisible(False)

        # ---- file & pages ------------------------------------------------
        def _error(self, text):
            QMessageBox.warning(self, TITLE, text)

        def _browse(self):
            start = self.p.get("last_dir") or str(Path.home())
            f, _ = QFileDialog.getOpenFileName(
                self, f"{TITLE} — open a plan or plan set", start,
                "Vector PDF (*.pdf);;All files (*)")
            if f:
                self.p["last_dir"] = str(Path(f).parent)
                self._open(f)

        def _open(self, f):
            if self._computing:
                return
            pv = _pv()
            try:
                pages = pv.page_info(f)
            except ImportError as exc:
                self._error("The PDF reader could not be loaded "
                            f"(missing module: {exc.name or exc}).\n"
                            "Please report this with your IngeTrazo "
                            "version.")
                return
            except Exception as exc:                       # noqa: BLE001
                self._error(str(exc))
                return
            self._timer.stop()
            self._undo_preview()
            self._pages = pages
            self._titles = {}
            self._vec = {}
            self._built = {}
            self._texts = {}
            self._images = {}
            self._shown = None
            self._levels = {}
            self._stories = []
            self._auto_elev = True
            self._mark = {}
            self._runs_cache = {}
            self._rowpage = list(range(len(pages)))
            self._crop = {}
            self._ref = {}
            self.ed_file.setText(f)
            self._busy = True
            for r in range(self.table.rowCount()):
                self._drop_widgets(r)
            self.table.setRowCount(0)
            self.table.setRowCount(len(pages))
            for r in range(len(pages)):
                self._fill_row(r, r)
            self._busy = False
            self.ok_btn.setEnabled(bool(pages))
            self.preview.setEnabled(bool(pages))
            self._sync_info()
            self._start_worker(f)
            if pages:
                self.table.setCurrentCell(0, COL_NAME)
                self._show_page(0)
            if self.preview.isChecked():
                self._changed()

        def _drop_widgets(self, r):
            """Take the cell widgets of row ``r`` out for good — a
            replaced or removed row otherwise left them behind as ghosts
            on the table (seen after opening a second file / Reset)."""
            for c in range(self.table.columnCount()):
                w = self.table.cellWidget(r, c)
                if w is not None:
                    self.table.removeCellWidget(r, c)
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()

        def _fill_row(self, r, k, src=None):
            """Widgets of table row ``r`` for page ``k`` (values copied
            from row ``src`` when given)."""
            pv = _pv()
            w, h = self._pages[k]
            use = QTableWidgetItem()
            use.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                         | Qt.ItemFlag.ItemIsEnabled)
            use.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(r, COL_USE, use)
            it = QTableWidgetItem(str(k + 1))
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            it.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(r, COL_PAGE, it)
            self.table.setItem(r, COL_NAME, QTableWidgetItem(""))
            cb = QComboBox()
            cb.addItems(TYPES)
            cb.setToolTip("What the sheet shows — read from its name or "
                          "content. In Stacked: floor plans stack, "
                          "elevations/sections stand upright around "
                          "the building.")
            cb.currentIndexChanged.connect(self._type_changed_sender)
            self.table.setCellWidget(r, COL_TYPE, cb)
            sp = QSpinBox()
            sp.setRange(1, 1_000_000)
            sp.setValue(int(self.p.get("scale", 100)))
            sp.setFrame(False)
            sp.valueChanged.connect(self._scale_typed_sender)
            self.table.setCellWidget(r, COL_SCALE, sp)
            el = QDoubleSpinBox()
            el.setRange(-10_000, 10_000)
            el.setDecimals(2)
            el.setSuffix(" m")
            el.setFrame(False)
            el.valueChanged.connect(self._elev_edited)
            self.table.setCellWidget(r, COL_ELEV, el)
            it = QTableWidgetItem("…")
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            it.setToolTip("Storey level read from the plan set "
                          "(story markers in sections/elevations, or "
                          "the plan's own level marker).")
            self.table.setItem(r, COL_LEVEL, it)
            it = QTableWidgetItem(
                f"{w * pv.PT_TO_MM:,.0f} × {h * pv.PT_TO_MM:,.0f} mm")
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            self.table.setItem(r, COL_PAPER, it)
            it = QTableWidgetItem("—")
            it.setFlags(Qt.ItemFlag.ItemIsEnabled)
            it.setToolTip("No reference point: Stacked places the sheet as "
                          "it lies on the paper. Set one with «Ref point».")
            self.table.setItem(r, COL_REF, it)
            if src is not None:
                self.table.item(r, COL_NAME).setText(
                    self.table.item(src, COL_NAME).text())
                cb.setCurrentText(
                    self.table.cellWidget(src, COL_TYPE).currentText())
                sp.setValue(self.table.cellWidget(src, COL_SCALE).value())
                el.setValue(self.table.cellWidget(src, COL_ELEV).value())
                lv = self.table.item(src, COL_LEVEL)
                self.table.item(r, COL_LEVEL).setText(lv.text())

        def _row_of(self, widget, col):
            for r in range(self.table.rowCount()):
                if self.table.cellWidget(r, col) is widget:
                    return r
            return -1

        def _type_changed_sender(self, *_a):
            r = self._row_of(self.sender(), COL_TYPE)
            if r >= 0:
                self._type_changed(r)

        def _scale_typed_sender(self, *_a):
            r = self._row_of(self.sender(), COL_SCALE)
            if r >= 0:
                self._typed(r, COL_SCALE)
            else:
                self._changed()

        def _pg(self, r):
            return self._rowpage[r] if 0 <= r < len(self._rowpage) else r

        def _rows_of_page(self, k):
            return [r for r, kk in enumerate(self._rowpage) if kk == k]

        def _page_label(self, r):
            k = self._pg(r)
            same = self._rows_of_page(k)
            if len(same) < 2:
                return str(k + 1)
            return f"{k + 1}.{same.index(r) + 1}"

        def _relabel_pages(self):
            for r in range(self.table.rowCount()):
                it = self.table.item(r, COL_PAGE)
                if it is not None:
                    it.setText(self._page_label(r))

        def _start_worker(self, f):
            """Off the main thread: read «Name 1:100» from every sheet, then
            the geometry of every page — so the preview later only builds."""
            self._scan_id += 1
            scan_id = self._scan_id
            n = len(self._pages)
            vec = self._vec                  # this file's own cache
            relay = self._relay
            self.lb_scan.setText(f"Reading titles… 0 / {n}")

            def work():
                pv = _pv()
                try:
                    reader = pv.open_reader(f)
                except Exception:                          # noqa: BLE001
                    relay.msg.emit((scan_id, "end", None, None))
                    return
                for i in range(n):
                    if scan_id != self._scan_id:
                        return
                    try:
                        name, scale, texts = pv.page_texts(f, i, reader)
                    except Exception:                      # noqa: BLE001
                        name, scale, texts = None, None, []
                    relay.msg.emit((scan_id, "title", i,
                                    (name, scale, texts)))
                for i in range(n):
                    if scan_id != self._scan_id:
                        return
                    if i not in vec:
                        try:
                            vec[i] = pv.extract(f, i)
                        except Exception:                  # noqa: BLE001
                            pass
                    relay.msg.emit((scan_id, "geom", i, None))
                relay.msg.emit((scan_id, "end", None, None))

            threading.Thread(target=work, daemon=True).start()

        def _on_worker(self, payload):
            scan_id, kind, i, data = payload
            if scan_id != self._scan_id:
                return                                     # an older file
            n = len(self._pages)
            if kind == "title":
                name, scale, texts = data
                self._titles[i] = (name, scale)
                self._texts[i] = texts
                for rr in self._rows_of_page(i):
                    item = self.table.item(rr, COL_NAME)
                    if item is not None and not item.text() and name:
                        self._busy = True
                        item.setText(name)
                        self._busy = False
                    if scale:
                        self._busy = True
                        self.table.cellWidget(rr, COL_SCALE).setValue(
                            int(scale))
                        self._busy = False
                        self._set_mark(rr, COL_SCALE, "sheet",
                                       f"Read from the sheet: 1:{scale}.")
                    if name:
                        self._set_mark(rr, COL_NAME, "sheet",
                                       "Drawing name read from the sheet.")
                self._changed()
                self.lb_scan.setText(f"Reading titles… {i + 1} / {n}")
                if i == self._shown:
                    self._images.pop(i, None)       # now with its texts
                    self._show_page(i, keep_view=True)
                if i == n - 1:
                    self._read_levels()
            elif kind == "geom":
                self.lb_scan.setText(f"Reading geometry… {i + 1} / {n}"
                                     + self._stories_text())
            else:
                self.lb_scan.setText("Pages read." + self._stories_text())

        # ---- page view ----------------------------------------------
        def _caption(self, i):
            name, scale = self._titles.get(i, (None, None))
            bits = [f"Page {i + 1}"]
            if name:
                bits.append(name)
            if i in self._titles:
                bits.append(f"scale on the sheet: 1:{scale}" if scale
                            else "no scale found on the sheet")
            else:
                bits.append("reading the texts…")
            if i in self._levels:
                v, src = self._levels[i]
                bits.append(f"level {self._fmt_level(v)} m ({src})")
            return " · ".join(bits)

        def _row_selected(self, r):
            if r < 0:
                return
            k = self._pg(r)
            if k != self._shown:
                self._show_page(k)
            else:
                self._update_crops()

        def _update_crops(self):
            k = self._shown
            if k is None:
                self.view.set_crops([])
                self.view.set_refs([])
                return
            cur = self.table.currentRow()
            crops = []
            for r in self._rows_of_page(k):
                if r in self._crop:
                    t = self.table.cellWidget(r, COL_TYPE).currentText()
                    crops.append((self._crop[r], r == cur,
                                  f"{self._page_label(r)} · {t}"))
            self.view.set_crops(crops)
            refs = [(self._ref[r], r == cur, f"Ref {self._page_label(r)}")
                    for r in self._rows_of_page(k) if r in self._ref]
            self.view.set_refs(refs)

        def _cur_row_of_shown(self):
            """The selected row when it belongs to the page shown, else the
            first row of that page."""
            r = self.table.currentRow()
            if r >= 0 and self._pg(r) == self._shown:
                return r
            rows = self._rows_of_page(self._shown)
            return rows[0] if rows else -1

        def _crop_clicked(self, on, new_row):
            btn = self.btn_view if new_row else self.btn_crop
            other = self.btn_crop if new_row else self.btn_view
            other.setChecked(False)
            if not on:
                self.view.cancel_rect()
                self.lb_measure.setText("")
                return
            r = self._cur_row_of_shown()
            if r < 0 or self._images.get(self._shown) is None:
                btn.setChecked(False)
                self._error("Choose a page in the list first — its sheet "
                            "must be shown in the page view.")
                return
            self.btn_measure.setChecked(False)
            ok = self.view.start_rect(
                lambda rect, row=r, nr=new_row: self._cropped(row, rect, nr))
            if not ok:
                btn.setChecked(False)
                return
            self.lb_measure.setText(
                f"Page {self._page_label(r)}: drag a frame around "
                + ("the next drawing." if new_row else "the drawing."))
            self._crop_watch = QTimer(self)
            self._crop_watch.setInterval(200)
            self._crop_watch.timeout.connect(self._crop_poll)
            self._crop_watch.start()

        def _crop_poll(self):
            if not self.view.drawing_rect():
                w = getattr(self, "_crop_watch", None)
                if w is not None:
                    w.stop()
                self.btn_crop.setChecked(False)
                self.btn_view.setChecked(False)

        def _insert_row_after(self, src):
            """A new table row for the same page right below ``src``."""
            at = src + 1
            k = self._pg(src)
            # shift row-keyed state
            self._crop = {(r + 1 if r >= at else r): v
                          for r, v in self._crop.items()}
            self._ref = {(r + 1 if r >= at else r): v
                         for r, v in self._ref.items()}
            self._mark = {((r + 1 if r >= at else r), c): v
                          for (r, c), v in self._mark.items()}
            self._rowpage.insert(at, k)
            self._busy = True
            self.table.insertRow(at)
            self._fill_row(at, k, src=src)
            self._busy = False
            # re-apply colours of shifted rows (widgets moved with them)
            for (r, c), v in list(self._mark.items()):
                if r > at:
                    self._set_mark(r, c, v)
            self._relabel_pages()
            return at

        def _cropped(self, row, rect, new_row):
            pv = _pv()
            if new_row and row in self._crop:
                row = self._insert_row_after(row)
                # next side of a sheet holding several views
                k = self._pg(row)
                kind, sides, _sure = pv.classify_page(
                    self._titles.get(k, (None, None))[0],
                    self._texts.get(k, []))
                part = self._rows_of_page(k).index(row)
                if kind == "elev" and len(sides) > 1:
                    self._busy = True
                    self.table.cellWidget(row, COL_TYPE).setCurrentText(
                        f"Elevation {sides[part % len(sides)]}")
                    self._busy = False
                    self._set_mark(row, COL_TYPE, "suggested",
                                   f"View {part + 1} of this sheet "
                                   f"({', '.join(sides)}) — check it.")
                else:
                    self._set_mark(row, COL_TYPE, "suggested",
                                   "A further drawing of this sheet — "
                                   "check its type.")
                for c in (COL_SCALE, COL_NAME):
                    v = self._mark.get((row - 1, c))
                    if v:
                        self._set_mark(row, c, v)
                self._sync_type_row(row)
            self._crop[row] = tuple(round(v, 2) for v in rect)
            if not getattr(self, "_busy_auto", False):
                self._set_mark(row, COL_PAGE, None)    # drawn by hand
            self.table.setCurrentCell(row, COL_NAME)
            self._update_crops()
            self._update_warning()
            w = getattr(self, "_crop_watch", None)
            if w is not None:
                w.stop()
            self.btn_crop.setChecked(False)
            self.btn_view.setChecked(False)
            pv_mm = pv.PT_TO_MM
            self.lb_measure.setText(
                f"Page {self._page_label(row)}: frame "
                f"{(rect[2] - rect[0]) * pv_mm:,.0f} × "
                f"{(rect[3] - rect[1]) * pv_mm:,.0f} mm on paper.")
            self._changed()

        def _sync_colour_btn(self):
            n = len(self.p.get("skip_colours") or ())
            self.btn_colours.setText(
                f"Colours… ({n} off)" if n else "Colours…")

        def _colours_dialog(self):
            """Tick the colours to import (all ticked sheets together)."""
            if self._computing or not self._pages:
                return
            from PySide6.QtGui import QColor, QIcon, QPixmap
            from PySide6.QtWidgets import (QDialogButtonBox, QListWidget,
                                           QListWidgetItem)
            from .cleanup import colour_table
            self._read_ui()
            base = {"strokes": self.p["strokes"], "fills": self.p["fills"]}
            table = {}
            pages = sorted({r["pg"] for r in self._rows() if r["use"]})
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                for k in pages:
                    for c, n in colour_table(self._vectors(k).paths,
                                             base).items():
                        table[c] = table.get(c, 0) + n
            finally:
                QApplication.restoreOverrideCursor()
            skip = set(self.p.get("skip_colours") or ())
            for c in skip:
                table.setdefault(c, 0)
            if not table:
                self._error("No colours on the ticked sheets.")
                return
            dlg = QDialog(self)
            dlg.setWindowTitle(f"{TITLE} — colours")
            lay = QVBoxLayout(dlg)
            lay.addWidget(QLabel(
                "Ticked colours are imported. Count = drawn parts on the "
                "ticked sheets.", dlg))
            lst = QListWidget(dlg)
            for c, n in sorted(table.items(), key=lambda kv: -kv[1]):
                pm = QPixmap(18, 18)
                pm.fill(QColor(c))
                it = QListWidgetItem(QIcon(pm), f"{c}   ({n:,})")
                it.setData(Qt.ItemDataRole.UserRole, c)
                it.setFlags(it.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                it.setCheckState(Qt.CheckState.Unchecked if c in skip
                                 else Qt.CheckState.Checked)
                lst.addItem(it)
            lay.addWidget(lst)
            b_all = QPushButton("All", dlg)
            b_none = QPushButton("None", dlg)

            def set_all(on):
                for i in range(lst.count()):
                    lst.item(i).setCheckState(
                        Qt.CheckState.Checked if on
                        else Qt.CheckState.Unchecked)

            b_all.clicked.connect(lambda: set_all(True))
            b_none.clicked.connect(lambda: set_all(False))
            bb = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Ok
                | QDialogButtonBox.StandardButton.Cancel, dlg)
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            lay.addWidget(row_widget(b_all, b_none))
            lay.addWidget(bb)
            dlg.resize(320, 420)
            self._colour_dlg = dlg               # for tests
            if dlg.exec() != QDialog.DialogCode.Accepted:
                return
            off = [lst.item(i).data(Qt.ItemDataRole.UserRole)
                   for i in range(lst.count())
                   if lst.item(i).checkState() != Qt.CheckState.Checked]
            self.p["skip_colours"] = sorted(off)
            self._sync_colour_btn()
            self._changed()

        def _auto_crop(self, all_ticked):
            """Crop frames suggested from the sheet(s) — see autocrop.py."""
            if self._computing or not self._pages:
                return
            from . import autocrop
            if all_ticked:
                pages = []
                for r in self._rows():
                    if r["use"] and r["pg"] not in pages:
                        pages.append(r["pg"])
            else:
                r = self._cur_row_of_shown()
                pages = [self._pg(r)] if r >= 0 else []
            if not pages:
                self._error("Choose a page in the list first.")
                return
            self.view.cancel_rect()
            self.btn_crop.setChecked(False)
            self.btn_view.setChecked(False)
            prog = None
            if len(pages) > 1:
                prog = QProgressDialog("Looking for the drawings…", None, 0,
                                       len(pages), self)
                prog.setWindowTitle(TITLE)
                prog.setWindowModality(Qt.WindowModality.WindowModal)
                prog.setMinimumDuration(0)
            done, none = 0, []
            self._busy_auto = True
            try:
                for n, k in enumerate(pages):
                    if prog is not None:
                        prog.setValue(n)
                        prog.setLabelText(
                            f"Page {k + 1} ({n + 1} of {len(pages)})…")
                    QApplication.processEvents(
                        QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
                    vec = self._vectors(k)
                    runs, _st = collect_runs(vec, {
                        "strokes": True, "fills": True, "skip_white": True})
                    rects = autocrop.suggest_crops(
                        vec.width, vec.height, runs, self._texts.get(k, []))
                    if not rects:
                        none.append(str(k + 1))
                        continue
                    rows = self._rows_of_page(k)
                    for j, rect in enumerate(rects):
                        if j < len(rows):
                            self._crop[rows[j]] = rect
                        else:
                            self._cropped(self._rows_of_page(k)[-1], rect,
                                          True)
                    for j, row in enumerate(self._rows_of_page(k)):
                        if j < len(rects):
                            self._set_mark(
                                row, COL_PAGE, "suggested",
                                "Crop frame suggested automatically "
                                f"({j + 1} of {len(rects)} on this sheet) "
                                "— check it in the page view; Crop draws "
                                "it anew.")
                    done += 1
            finally:
                self._busy_auto = False
                if prog is not None:
                    prog.close()
            k = pages[0]
            first = self._rows_of_page(k)
            if first:
                self.table.setCurrentCell(first[0], COL_NAME)
            if self._shown != k:
                self._show_page(k)
            self._update_crops()
            self._update_warning()
            t = f"Auto crop: frames suggested on {done} sheet(s)"
            if none:
                t += " · no drawing found on page(s) " + ", ".join(none)
            self.lb_measure.setText(t + " — please check them.")
            self._changed()

        def _clear_crop(self):
            r = self._cur_row_of_shown()
            if r < 0:
                return
            k = self._pg(r)
            if self._rows_of_page(k).index(r) > 0:
                # an added view row: remove it
                self._drop_widgets(r)
                self.table.removeRow(r)
                del self._rowpage[r]
                self._crop = {(rr - 1 if rr > r else rr): v
                              for rr, v in self._crop.items() if rr != r}
                self._ref = {(rr - 1 if rr > r else rr): v
                             for rr, v in self._ref.items() if rr != r}
                self._mark = {((rr - 1 if rr > r else rr), c): v
                              for (rr, c), v in self._mark.items()
                              if rr != r}
                self._relabel_pages()
                self.table.setCurrentCell(max(0, r - 1), COL_NAME)
            else:
                self._crop.pop(r, None)
                self._set_mark(r, COL_PAGE, None)
            self._update_crops()
            self._update_warning()
            self.lb_measure.setText("")
            self._changed()

        def _show_page(self, i, keep_view=False):
            """Draw sheet ``i`` in the page view (rendered once, kept for
            the last few pages)."""
            if not (0 <= i < len(self._pages)):
                return
            self._shown = i
            img = self._images.get(i)
            if img is None:
                if self._vec.get(i) is None:
                    self.view.set_message(f"Reading page {i + 1}…")
                    QTimer.singleShot(30, lambda: self._render_page(i))
                    return
                self._render_page(i, keep_view)
                return
            self.view.set_image(img, self._caption(i), keep_view,
                                page=self._pages[i])
            self._update_crops()

        def _render_page(self, i, keep_view=False):
            if i != self._shown:
                return
            try:
                vec = self._vectors(i)
                img = render_page(vec, self._texts.get(i, []))
            except Exception as exc:                       # noqa: BLE001
                self.view.set_message(f"Page {i + 1}: {exc}")
                return
            if len(self._images) >= 3:                     # ~45 MB each
                self._images.pop(next(iter(self._images)))
            self._images[i] = img
            self.view.set_image(img, self._caption(i), keep_view,
                                page=self._pages[i])
            self._update_crops()

        # ---- measure scale -----------------------------------------
        _NICE = (1, 2, 5, 10, 20, 25, 50, 75, 100, 125, 150, 200, 250, 300,
                 400, 500, 750, 1000, 1250, 1500, 2000, 2500, 5000, 10000)

        def _measure_clicked(self, on):
            if not on:
                self.view.cancel_measure()
                self._measure_done("")
                return
            i = self._shown
            if i is None or self._images.get(i) is None:
                self.btn_measure.setChecked(False)
                self._error("Choose a page in the list first — its sheet "
                            "must be shown in the page view.")
                return
            try:
                snaps = page_points(self._vectors(i))
            except Exception:                              # noqa: BLE001
                snaps = None
            ok = self.view.start_measure(
                snaps, lambda d, a, b, row=self._cur_row_of_shown():
                self._measured(row, d))
            if not ok:
                self.btn_measure.setChecked(False)
                return
            self.lb_measure.setText(
                f"Page {i + 1}: click the start and the end of a known "
                "distance (a wall, a dimension). Zoom in for accuracy.")
            self._measure_watch = QTimer(self)
            self._measure_watch.setInterval(200)
            self._measure_watch.timeout.connect(self._measure_poll)
            self._measure_watch.start()

        def _ref_clicked(self, on):
            if not on:
                self.view.cancel_measure()
                self._ref_done("")
                return
            self.btn_measure.setChecked(False)
            self.btn_crop.setChecked(False)
            self.btn_view.setChecked(False)
            r = self._cur_row_of_shown()
            if r < 0 or self._images.get(self._shown) is None:
                self.btn_ref.setChecked(False)
                self._error("Choose a page in the list first — its sheet "
                            "must be shown in the page view.")
                return
            try:
                snaps = page_points(self._vectors(self._shown))
            except Exception:                              # noqa: BLE001
                snaps = None
            ok = self.view.start_pick(
                snaps, lambda pt, row=r: self._ref_set(row, pt))
            if not ok:
                self.btn_ref.setChecked(False)
                return
            self.lb_measure.setText(
                f"Page {self._page_label(r)}: click the reference point — "
                "the same building corner on every sheet. Zoom in for "
                "accuracy.")
            self._ref_watch = QTimer(self)
            self._ref_watch.setInterval(200)
            self._ref_watch.timeout.connect(self._ref_poll)
            self._ref_watch.start()

        def _ref_poll(self):
            if not self.view.picking() and self.btn_ref.isChecked():
                self._ref_done("")

        def _ref_done(self, text):
            w = getattr(self, "_ref_watch", None)
            if w is not None:
                w.stop()
            self.btn_ref.setChecked(False)
            self.lb_measure.setText(text)

        def _show_ref(self, r):
            it = self.table.item(r, COL_REF)
            if it is None:
                return
            pt = self._ref.get(r)
            was = self._busy
            self._busy = True
            if pt is None:
                it.setText("—")
                it.setToolTip("No reference point: Stacked places the "
                              "sheet as it lies on the paper. Set one with "
                              "«Ref point».")
                self._busy = was
                self._set_mark(r, COL_REF, None)
                return
            mm = _pv().PT_TO_MM
            it.setText(f"✓ {pt[0] * mm:,.0f} / {pt[1] * mm:,.0f}")
            it.setToolTip("Reference point on the sheet (mm from the lower "
                          "left of the paper). «Ref point» ▸ menu clears "
                          "it.")
            self._busy = was
            self._set_mark(r, COL_REF, "measured")

        def _ref_set(self, row, pt):
            self._ref[row] = (round(pt[0], 2), round(pt[1], 2))
            self._show_ref(row)
            self._update_crops()
            mm = _pv().PT_TO_MM
            self._ref_done(
                f"Page {self._page_label(row)}: reference point at "
                f"{pt[0] * mm:,.1f} / {pt[1] * mm:,.1f} mm on the paper.")
            self._update_warning()
            self._changed()

        def _clear_ref(self, all_rows=False):
            if all_rows:
                rows = list(self._ref)
            else:
                r = self._cur_row_of_shown()
                rows = [r] if r in self._ref else []
            for r in rows:
                self._ref.pop(r, None)
                self._show_ref(r)
            if rows:
                self._update_crops()
                self._update_warning()
                self._changed()

        def _measure_poll(self):
            """Esc or a page change ended the measuring: reset the button."""
            if not self.view.measuring() and self.btn_measure.isChecked():
                self._measure_done("")

        def _measure_done(self, text):
            w = getattr(self, "_measure_watch", None)
            if w is not None:
                w.stop()
            self.btn_measure.setChecked(False)
            self.lb_measure.setText(text)

        def _measured(self, row, dist_pt):
            pv = _pv()
            paper_mm = dist_pt * pv.PT_TO_MM
            sp = self.table.cellWidget(row, COL_SCALE)
            guess = paper_mm * sp.value() / 1000.0
            real, ok = QInputDialog.getDouble(
                self, f"{TITLE} — Measure scale",
                f"Page {row + 1}: the distance is {paper_mm:.1f} mm on "
                "paper.\nReal length of this distance (m):",
                round(guess, 3), 0.001, 100000.0, 3)
            if not ok or real <= 0:
                self._measure_done("Measuring cancelled.")
                return
            exact = real * 1000.0 / paper_mm
            n = int(round(exact))
            for nice in self._NICE:
                if abs(exact - nice) / nice <= 0.015:
                    n = nice
                    break
            n = max(1, n)
            self._busy = True
            sp.setValue(n)
            self._busy = False
            tip = (f"Measured: {real:.3f} m = {paper_mm:.1f} mm on paper "
                   f"→ 1:{exact:.1f}" + (f", set to 1:{n}" if
                                        abs(exact - n) > 0.05 else "") + ".")
            self._set_mark(row, COL_SCALE, "measured", tip)
            self._update_warning()
            self._measure_done(f"Page {row + 1}: 1:{n}  ({tip})")
            self._changed()

        def _check_all(self, on):
            st = Qt.CheckState.Checked if on else Qt.CheckState.Unchecked
            self._busy = True
            for r in range(self.table.rowCount()):
                self.table.item(r, COL_USE).setCheckState(st)
            self._busy = False
            self._update_warning()
            self._changed()

        # ---- settings ----------------------------------------------------
        def _read_ui(self):
            self.p.update({
                "file": self.ed_file.text(),
                "strokes": self.cb_strokes.isChecked(),
                "fills": self.cb_fills.isChecked(),
                "skip_white": self.cb_white.isChecked(),
                "layout": "stack" if self.rb_stack.isChecked() else "side",
                "floor_h": self.sp_floor.value(),
                "base_z": self.sp_base.value(),
                "view_dist": self.sp_dist.value(),
                "to_levels": self.cb_levels.isChecked(),
                "to_layers": self.cb_layers.isChecked(),
                "min_part_mm": self.sp_small.value(),
                "min_width_mm": self.sp_thin.value(),
                "skip_hatch": self.cb_hatch.isChecked(),
                "dedupe": self.cb_dedupe.isChecked(),
                "gap_mm": self.sp_gap.value(),
            })

        def _rows(self):
            """Every page: ``{page, use, name, scale, elev}``."""
            rows = []
            for r in range(self.table.rowCount()):
                rows.append({
                    "row": r,
                    "pg": self._pg(r),
                    "page": self._page_label(r),
                    "crop": self._crop.get(r),
                    "use": self.table.item(r, COL_USE).checkState()
                    == Qt.CheckState.Checked,
                    "name": self.table.item(r, COL_NAME).text(),
                    "scale": self.table.cellWidget(r, COL_SCALE).value(),
                    "elev": self.table.cellWidget(r, COL_ELEV).value(),
                    "type": self.table.cellWidget(r, COL_TYPE).currentText(),
                    "ref": self._ref.get(r),
                })
            return rows

        def _state_key(self):
            """What the result depends on — file, pages and options."""
            self._read_ui()
            p = {k: self.p[k] for k in ("layout", "view_dist", "to_levels",
                                        "to_layers")}
            p["content"] = content_opts(self.p)
            return json.dumps([self.p["file"], self._rows(), p],
                              sort_keys=True)

        def _sync_info(self):
            rows = self._rows()
            n = sum(1 for r in rows if r["use"])
            self.lb_info.setText(
                f"{n} of {len(rows)} page(s) ticked. Name, scale and "
                "elevation can be edited; switch the Preview on to see the "
                "import in the model.")

        def _changed(self, *_a):
            if self._busy:
                return
            self._sync_info()
            if self.preview.isChecked():
                self._banner("busy", "⟳ Updating the preview…")
                self._timer.start()

        def _elev_edited(self, *_a):
            if not self._busy:
                self._auto_elev = False     # typed by hand: keep it
                r = self.table.currentRow()
                w = self.sender()
                for row in range(self.table.rowCount()):
                    if self.table.cellWidget(row, COL_ELEV) is w:
                        r = row
                        break
                self._set_mark(r, COL_ELEV, None)
                self._update_warning()
            self._changed()

        # ---- marks: read from the sheet / suggested / typed -------------
        _BG = {"sheet": "#dff3df", "suggested": "#ffd9a8",
               "measured": "#d6e6ff"}

        def _set_mark(self, r, col, kind, tip=None):
            """Colour one cell: green = read from the sheet, orange =
            suggested (please check), none = typed by hand / n/a."""
            if r is None or r < 0 or r >= self.table.rowCount():
                return
            if kind is None:
                self._mark.pop((r, col), None)
            else:
                self._mark[(r, col)] = kind
            bg = self._BG.get(kind)
            w = self.table.cellWidget(r, col)
            if w is not None:
                w.setStyleSheet(
                    f"background: {bg}; color: #111;" if bg else "")
                if tip is not None:
                    w.setToolTip(tip)
                return
            it = self.table.item(r, col)
            if it is None:
                return
            was = self._busy
            self._busy = True
            from PySide6.QtGui import QBrush, QColor
            if bg:
                it.setBackground(QBrush(QColor(bg)))
                it.setForeground(QBrush(QColor("#111111")))
            else:
                it.setData(Qt.ItemDataRole.BackgroundRole, None)
                it.setData(Qt.ItemDataRole.ForegroundRole, None)
            if tip is not None:
                it.setToolTip(tip)
            self._busy = was

        def _typed(self, r, col):
            """A value typed by hand: no longer a suggestion."""
            if not self._busy:
                self._set_mark(r, col, None)
                self._update_warning()
            self._changed()

        def _item_changed(self, item):
            if not self._busy and item is not None:
                if item.column() == COL_NAME:
                    self._set_mark(item.row(), COL_NAME, None)
                if item.column() in (COL_NAME, COL_USE):
                    self._update_warning()
            self._changed()

        def _update_warning(self):
            """One line: which ticked pages carry suggestions."""
            kinds = {COL_NAME: "name", COL_SCALE: "scale",
                     COL_ELEV: "elevation", COL_TYPE: "type"}
            by = {}
            for (r, col), kind in self._mark.items():
                if kind != "suggested" or col not in kinds:
                    continue
                if self.table.item(r, COL_USE).checkState() != \
                        Qt.CheckState.Checked:
                    continue
                by.setdefault(kinds[col], []).append(self._page_label(r))
            # reference points on some ticked sheets only
            noref = []
            if self._ref:
                for r in range(self.table.rowCount()):
                    if r in self._ref or self.table.item(r, COL_USE) \
                            .checkState() != Qt.CheckState.Checked:
                        continue
                    noref.append(self._page_label(r))
            if not by and not noref:
                self.lb_warn.setVisible(False)
                return
            text = ""
            if by:
                parts = [f"{k} on page {', '.join(str(p) for p in v)}"
                         for k, v in by.items()]
                text = ("⚠ Not found on the sheets — suggested, please "
                        "check (orange): " + " · ".join(parts) + ". Use "
                        "the page view to check; typing a value clears the "
                        "mark.")
            if noref:
                text += ((" " if text else "⚠ ") +
                         "No reference point on page "
                         + ", ".join(str(p) for p in noref)
                         + " — those stay where they lie on the paper.")
            self.lb_warn.setText(text)
            self.lb_warn.setVisible(True)

        def _sync_type_row(self, r):
            """Upright views take no storey elevation: grey it out."""
            t = self.table.cellWidget(r, COL_TYPE).currentText()
            el = self.table.cellWidget(r, COL_ELEV)
            up = t in UPRIGHT
            el.setEnabled(not up)
            if up:
                el.setToolTip("Stands upright in Stacked — its ±0,00 line "
                              "goes to z = 0 (else its lowest point to "
                              "Base).")

        def _type_changed(self, r):
            if self._busy:
                return
            self._set_mark(r, COL_TYPE, None)
            self._sync_type_row(r)
            self._update_crops()
            lvl = self.table.item(r, COL_LEVEL)
            t = self.table.cellWidget(r, COL_TYPE).currentText()
            if t != "Floor plan" and lvl is not None:
                lvl.setText("n/a")
                self._set_mark(r, COL_LEVEL, None)
            self._update_warning()
            self._changed()

        def _mark_missing(self):
            """After all titles: suggest what the sheets did not tell."""
            pv = _pv()
            scales = [sc for _n, sc in self._titles.values() if sc]
            common = max(set(scales), key=scales.count) if scales else None
            path = self.ed_file.text()
            for r in range(self.table.rowCount()):
                k = self._pg(r)
                name, scale = self._titles.get(k, (None, None))
                if not scale and self._mark.get((r, COL_SCALE)) != \
                        "measured":
                    sp = self.table.cellWidget(r, COL_SCALE)
                    if common:
                        self._busy = True
                        sp.setValue(int(common))
                        self._busy = False
                    self._set_mark(
                        r, COL_SCALE, "suggested",
                        "No scale found on this sheet — "
                        + (f"1:{common} suggested (the usual scale of this "
                           "set)." if common else "please enter it.")
                        + " Check it in the page view.")
                if not name:
                    self._set_mark(
                        r, COL_NAME, "suggested",
                        "No drawing name found on the sheet — the group "
                        f"will be called «{Path(path).stem} p{k + 1}». "
                        "Type a name to change it.")
                # ---- what the sheet shows (check routine) ----
                kind, sides, sure = pv.classify_page(
                    name, self._texts.get(k, []))
                same = self._rows_of_page(k)
                part = same.index(r)
                if kind == "elev" and len(sides) > 1:
                    ptype = f"Elevation {sides[part % len(sides)]}"
                else:
                    ptype = type_from_class(kind, sides)
                cb = self.table.cellWidget(r, COL_TYPE)
                self._busy = True
                cb.setCurrentText(ptype)
                self._busy = False
                if len(sides) > 1 and len(same) >= len(sides) \
                        and r in self._crop:
                    self._set_mark(
                        r, COL_TYPE, "sheet",
                        f"View {part + 1} of {len(sides)} on this sheet "
                        f"({', '.join(sides)}) — check that the crop "
                        "frame holds this one.")
                elif len(sides) > 1:
                    self._set_mark(
                        r, COL_TYPE, "suggested",
                        f"This sheet holds {len(sides)} views "
                        f"({', '.join(sides)}) — it stands as "
                        f"{ptype}. Draw one crop frame per view: «Crop» "
                        "for this row, «+ View» for the next one.")
                elif sure:
                    self._set_mark(r, COL_TYPE, "sheet",
                                   "Read from the drawing name.")
                else:
                    self._set_mark(
                        r, COL_TYPE, "suggested",
                        "Guessed from the content (no telling name) — "
                        "check it in the page view and pick the right "
                        "type.")
                self._sync_type_row(r)
                lvl = self.table.item(r, COL_LEVEL)
                if k in self._levels and ptype == "Floor plan":
                    v, src = self._levels[k]
                    lvl.setText(f"{self._fmt_level(v)} m")
                    self._set_mark(r, COL_LEVEL, "sheet",
                                   f"Read from the sheets: {src}.")
                elif ptype != "Floor plan":
                    lvl.setText("n/a")
                    lvl.setToolTip("Not a floor plan (section, elevation, "
                                   "site plan) — no storey level.")
                    self._set_mark(r, COL_LEVEL, None)
                else:
                    lvl.setText("?")
                    self._set_mark(
                        r, COL_LEVEL, "suggested",
                        "No storey level found for this page — Fill "
                        "elevations suggests the previous storey + Floor "
                        "height. Check it against a section.")
            self._update_warning()
            self._changed()

        def _fill_elevations(self):
            """Ticked pages in list order: the level read from the sheets
            where there is one, else the previous page + Floor height (the
            first one: Base)."""
            h = self.sp_floor.value()
            last = None
            self._busy = True
            for r in range(self.table.rowCount()):
                if self.table.item(r, COL_USE).checkState() != \
                        Qt.CheckState.Checked:
                    continue
                k = self._pg(r)
                ptype = self.table.cellWidget(r, COL_TYPE).currentText()
                if ptype != "Floor plan":
                    # section / elevation / site plan: not a storey
                    self.table.cellWidget(r, COL_ELEV).setValue(0.0)
                    self._set_mark(r, COL_ELEV, None,
                                   "Not a floor plan — placed at 0 m.")
                    continue
                if k in self._levels:
                    z = self._levels[k][0]
                    kind = "sheet"
                    tip = f"Read from the sheets: {self._levels[k][1]}."
                elif last is None:
                    z = self.sp_base.value()
                    kind, tip = "suggested", "Suggested: Base."
                else:
                    z = last + h
                    kind = "suggested"
                    tip = (f"Suggested: previous storey + Floor height "
                           f"({h:.2f} m). Please check.")
                self.table.cellWidget(r, COL_ELEV).setValue(z)
                self._set_mark(r, COL_ELEV, kind, tip)
                last = z
            self._busy = False
            self._update_warning()
            self._changed()

        @staticmethod
        def _fmt_level(v):
            return "±0.00" if abs(v) < 0.005 else f"{v:+.2f}"

        def _stories_text(self):
            if not self._stories:
                return ""
            return ("  ·  Storeys on the sheets: "
                    + ", ".join(f"{lb} {self._fmt_level(v)}"
                                for lb, v in self._stories))

        def _read_levels(self):
            """Storey levels from the whole set (after all titles)."""
            pv = _pv()
            names = {r: self._titles.get(r, (None, None))[0]
                     for r in range(len(self._pages))}
            try:
                levels, typical, stories = pv.suggest_levels(
                    names, self._texts)
            except Exception:                              # noqa: BLE001
                levels, typical, stories = {}, None, []
            self._levels, self._stories = levels, stories
            for r in range(self.table.rowCount()):
                it = self.table.item(r, COL_LEVEL)
                if self._pg(r) in levels:
                    v, src = levels[self._pg(r)]
                    it.setText(f"{self._fmt_level(v)} m")
                    it.setToolTip(f"Read from the sheets: {src}.")
                else:
                    it.setText("—")
                    it.setToolTip("No storey level found for this page.")
            if typical:
                self._busy = True
                self.sp_floor.setValue(typical)
                self._busy = False
                self.sp_floor.setToolTip(
                    f"Default storey height for Stacked — {typical:.2f} m "
                    "read from the storey markers on the sheets.")
            self._mark_missing()
            if self.rb_stack.isChecked() and (self._auto_elev or all(
                    abs(self.table.cellWidget(r, COL_ELEV).value()) < 1e-9
                    for r in range(self.table.rowCount()))):
                self._fill_elevations()
            if self._shown is not None:
                self.view._caption = self._caption(self._shown)
                self.view.update()

        def _stack_toggled(self, on):
            """Switching to Stacked while every elevation is still 0 fills
            them with the default storey height at once."""
            if not on or self._busy or not self.table.rowCount():
                return
            if all(abs(self.table.cellWidget(r, COL_ELEV).value()) < 1e-9
                   for r in range(self.table.rowCount())):
                self._fill_elevations()

        def _reset(self):
            p = default_params()
            for k in ("layout",) + CONTENT_KEYS:
                self.p[k] = p[k]
            self._options_into_ui()
            self._busy = True
            # back to one row per page, no crop frames
            for r in range(self.table.rowCount() - 1, -1, -1):
                if self._rows_of_page(self._pg(r)).index(r) > 0:
                    self._drop_widgets(r)
                    self.table.removeRow(r)
                    del self._rowpage[r]
            self._crop = {}
            self._ref = {}
            self._mark = {k: v for k, v in self._mark.items()
                          if k[0] < self.table.rowCount()}
            self._relabel_pages()
            for r in range(self.table.rowCount()):
                name, scale = self._titles.get(self._pg(r), (None, None))
                self.table.item(r, COL_USE).setCheckState(
                    Qt.CheckState.Checked)
                self.table.item(r, COL_NAME).setText(name or "")
                self.table.cellWidget(r, COL_SCALE).setValue(
                    int(scale or p["scale"]))
                self.table.cellWidget(r, COL_ELEV).setValue(0.0)
                self._set_mark(r, COL_ELEV, None)
            self._busy = False
            self._auto_elev = True
            if self._titles:
                self._mark_missing()
            if self._shown is not None:
                self._show_page(self._shown, keep_view=True)
            self._changed()

        # ---- building ----------------------------------------------------
        def _vectors(self, i):
            v = self._vec.get(i)
            if v is None:                    # not read in the background yet
                v = self._vec[i] = _pv().extract(self.ed_file.text(), i)
            return v

        def _runs(self, row, opts):
            """Runs + bbox (sheet points) of a table row — its page, cut
            to its crop frame — kept per options."""
            cache = self.__dict__.setdefault("_runs_cache", {})
            k, crop = row["pg"], row["crop"]
            key = (self.ed_file.text(), k, crop, opts, row["scale"])
            if key not in cache:
                runs, st = collect_runs(self._vectors(k), self.p)
                runs = clip_runs(runs, crop)
                runs, st2 = clean_runs(runs, self.p,
                                       metres_per_point(row["scale"]))
                st.update(st2)
                self.__dict__.setdefault("_run_stats", {})[key] = st
                cache[key] = (runs, runs_bbox(runs))
            return cache[key]

        def _row_texts(self, row):
            texts = self._texts.get(row["pg"], [])
            c = row["crop"]
            if c is None:
                return texts
            return [t for t in texts
                    if c[0] <= t.x <= c[2] and c[1] <= t.y <= c[3]]

        def _placements(self, rows, opts):
            """``{row: (offset, axes)}`` for the ticked rows.

            Side by side: every sheet flat in a row along red. Stacked:
            floor plans / site plan / other at the origin on their
            Elevation; elevations and sections upright around the floor
            plans' footprint at View distance (a second view on the same
            side one distance further out)."""
            ticked = [(k, r) for k, r in enumerate(rows) if r["use"]]
            if self.p["layout"] != "stack":
                sheets = [(((r["crop"][2] - r["crop"][0]) if r["crop"]
                            else self._pages[r["pg"]][0])
                           * metres_per_point(r["scale"]), 0.0)
                          for r in rows]
                offs = layout_offsets(sheets, "side")
                return {k: (offs[k], FLAT) for k, _r in ticked}
            out = {}
            box = None
            for k, r in ticked:
                if r["type"] in UPRIGHT:
                    continue
                mpp = metres_per_point(r["scale"])
                dx = dy = 0.0
                if r["ref"] is not None:      # reference point → origin
                    dx, dy = -r["ref"][0] * mpp, -r["ref"][1] * mpp
                out[k] = ((dx, dy, float(r["elev"])), FLAT)
                if r["type"] == "Floor plan":
                    _runs, bb = self._runs(r, opts)
                    if bb is not None:
                        b = (bb[0] * mpp + dx, bb[1] * mpp + dy,
                             bb[2] * mpp + dx, bb[3] * mpp + dy)
                        box = b if box is None else (
                            min(box[0], b[0]), min(box[1], b[1]),
                            max(box[2], b[2]), max(box[3], b[3]))
            if box is None:
                box = (0.0, 0.0, 10.0, 10.0)
            plan_z = [r["elev"] for k, r in ticked
                      if r["type"] == "Floor plan"]
            base_z = min(plan_z) if plan_z else self.sp_base.value()
            slots = {}
            pv = _pv()
            # elevations nearest to the building, sections further out
            upright = sorted((kr for kr in ticked if kr[1]["type"] in UPRIGHT),
                             key=lambda kr: kr[1]["type"] == "Section")
            for k, r in upright:
                side = UPRIGHT.get(r["type"])
                if side is None:
                    continue
                _runs, bb = self._runs(r, opts)
                if bb is None:
                    continue
                mpp = metres_per_point(r["scale"])
                bbm = tuple(c * mpp for c in bb)
                dy = pv.datum_y(self._row_texts(r))
                datum_m = dy * mpp if dy is not None else None
                slot = slots.get(side, 0)
                slots[side] = slot + 1
                ref_m = None
                if r["ref"] is not None:
                    ref_m = (r["ref"][0] * mpp, r["ref"][1] * mpp)
                out[k] = upright_placement(side, slot, box, bbm, datum_m,
                                           base_z, self.p["view_dist"],
                                           ref_m)
            return out

        def _build(self, progress):
            """The Group of every ticked page — from the cache when page,
            scale, content and placement are unchanged. Returns
            ``(groups, empty_pages)``."""
            self._read_ui()
            path = self.p["file"]
            rows = self._rows()
            opts = content_opts(self.p)
            place = self._placements(rows, opts)
            ticked = [(k, r) for k, r in enumerate(rows) if r["use"]]
            # Inside an open group the insert re-expresses a group in the
            # container's space (it changes the mesh): no reuse there.
            if getattr(self.viewport.scene, "edit_group", None) is not None:
                self._built.clear()
            groups, empty = [], []
            self._layer_names = []
            for n, (k, r) in enumerate(ticked):
                progress(n, len(ticked), r["page"])
                if k not in place:
                    empty.append(r["page"])
                    continue
                off, axes = place[k]
                off = tuple(round(c, 6) for c in off)
                key = (r["pg"], r["crop"], r["scale"], opts, off, axes)
                g = self._built.get(key)
                if g is None:
                    runs, _bb = self._runs(r, opts)
                    if not runs:
                        empty.append(r["page"])
                        continue
                    g = build_group(runs, metres_per_point(r["scale"]),
                                    "", off, axes)
                    # How every line was drawn, for plugins like Walls
                    # (the edges themselves keep no style).
                    try:
                        rec = style_record(self._vectors(r["pg"]), self.p,
                                           r["crop"])
                        rec.update({"version": VERSION, "page": r["page"],
                                    "scale": r["scale"],
                                    "mpp": metres_per_point(r["scale"]),
                                    "offset": list(off),
                                    "axes": [list(a) for a in axes]})
                        g.ext = {KEY: rec}
                    except Exception:                      # noqa: BLE001
                        pass                 # the import works without it
                    self._built[key] = g
                name = r["name"]
                same = self._rows_of_page(r["pg"])
                if name and len(same) > 1:
                    kinds = [self.table.cellWidget(q, COL_TYPE).currentText()
                             for q in same]
                    if kinds.count(r["type"]) > 1:
                        n_ = [q for q, t in zip(same, kinds)
                              if t == r["type"]].index(r["row"]) + 1
                        name = f"{name} ({r['type']} {n_})"
                    else:
                        name = f"{name} ({r['type']})"
                g.name = sheet_name(path, r["page"], name, r["scale"])
                groups.append(g)
                self._layer_names.append(layer_name(path, r["page"], name))
            return groups, empty

        def _level_list(self):
            """[(name, z)] for the Levels panel: the storeys on the sheets,
            plus the ticked floor plans' elevations not among them."""
            out = [(lb, float(v)) for lb, v in self._stories]
            for r in self._rows():
                if not r["use"] or r["type"] != "Floor plan":
                    continue
                z = float(r["elev"])
                if any(abs(z - v) <= 0.01 for _n, v in out):
                    continue
                name = r["name"].strip() or f"Page {r['page']}"
                out.append((name, z))
            return out

        def _levels_commands(self):
            if self.p.get("layout") != "stack" or \
                    not self.p.get("to_levels", True):
                return []
            new = self._level_list()
            if not new:
                return []
            from core.history import SetPluginDataCommand
            old = (getattr(self.viewport.scene, "plugin_data", {}) or {}
                   ).get(LEVELS_KEY)
            return [SetPluginDataCommand(LEVELS_KEY,
                                         merge_levels(old, new))]

        def _run(self, progress, select):
            self._computing = True
            t0 = time.perf_counter()
            try:
                groups, empty = self._build(progress)
                if not groups:
                    raise ValueError("Nothing to import on the ticked pages "
                                     "with these options.")
                extra = []
                if self.p.get("to_layers", True):
                    extra += layer_commands(groups, self._layer_names)
                cmd = insert_groups(self.viewport, groups, select,
                                    extra + self._levels_commands())
            finally:
                self._computing = False
            cmd.key = self._state_key()
            cmd.info = {"sheets": len(groups),
                        "edges": sum(len(g.mesh.edges) for g in groups),
                        "seconds": time.perf_counter() - t0,
                        "empty": empty,
                        "filtered": self._filter_totals()}
            return cmd

        def _filter_totals(self):
            """Sum of what the filters and the clean up did on the ticked
            rows (from the run cache)."""
            stats = self.__dict__.get("_run_stats", {})
            opts = content_opts(self.p)
            tot = {}
            for r in self._rows():
                if not r["use"]:
                    continue
                key = (self.ed_file.text(), r["pg"], r["crop"], opts,
                       r["scale"])
                for k, v in (stats.get(key) or {}).items():
                    if k in ("hatch", "small", "thin", "colour", "merged",
                             "closed"):
                        tot[k] = tot.get(k, 0) + v
            return tot

        def _result_text(self, info):
            t = (f"{info['sheets']} sheet(s), {info['edges']:,} edges "
                 f"({info['seconds']:.1f} s)")
            f = info.get("filtered") or {}
            parts = [f"{f[k]:,} {lb}" for k, lb in (
                ("hatch", "hatch lines"), ("small", "small parts"),
                ("thin", "thin lines"), ("colour", "by colour"),
                ("merged", "overlaps merged"), ("closed", "gaps closed"))
                if f.get(k)]
            if parts:
                t += " · " + ", ".join(parts)
            if info["empty"]:
                t += (" · nothing on page(s) "
                      + ", ".join(str(p) for p in info["empty"]))
            return t

        # ---- preview -----------------------------------------------------
        def _undo_preview(self):
            cmd = self._preview_cmd
            self._preview_cmd = None
            if cmd is None:
                return
            stack = getattr(self.viewport.history, "undo_stack", [])
            if stack and stack[-1] is cmd:
                self.viewport.history.undo()
                notify = getattr(self.viewport, "notify_scene_changed", None)
                if notify:
                    notify()
                self.viewport.update()

        def _preview_progress(self, n, total, page):
            self._banner("busy", f"⟳ Building the preview… page {page} "
                         f"({n + 1} of {total})")
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

        def _refresh_preview(self):
            if self._computing:            # a change during a build: later
                self._timer.start()
                return
            self._undo_preview()
            if not self.preview.isChecked() or not self._pages:
                return
            if not any(r["use"] for r in self._rows()):
                self._banner("paused", "⏸ <b>Preview paused</b> — no page "
                             "ticked. It comes back by itself.")
                return
            self._banner("busy", "⟳ Updating the preview…")
            QApplication.processEvents(
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
            try:
                self._preview_cmd = self._run(self._preview_progress, False)
                self._banner("live", "● <b>LIVE PREVIEW</b> — "
                             + self._result_text(self._preview_cmd.info)
                             + " · changes apply instantly")
            except Exception as exc:                       # noqa: BLE001
                self._preview_cmd = None
                self._banner("error", f"Preview failed: {exc}")

        def _preview_toggled(self, on):
            self._style_preview(on)
            if on:
                self._refresh_preview()
            else:
                self._timer.stop()
                self._undo_preview()

        def _keep_preview(self):
            """The live preview already IS the result for these settings:
            keep it (one undo step already) instead of building again."""
            cmd = self._preview_cmd
            if cmd is None or getattr(cmd, "key", None) != self._state_key():
                return None
            stack = getattr(self.viewport.history, "undo_stack", [])
            if not stack or stack[-1] is not cmd:
                return None
            scene = self.viewport.scene
            cmd.select = True               # redo/undo now behave like OK
            scene.selection.clear()
            for g in cmd.groups:
                scene.selection.add(g)
            scene.version += 1
            self._preview_cmd = None
            notify = getattr(self.viewport, "notify_scene_changed", None)
            if notify:
                notify()
            self.viewport.update()
            return cmd

        # ---- close -------------------------------------------------------
        def accept(self):
            if self._computing or not self._pages:
                return
            if not any(r["use"] for r in self._rows()):
                self._error("Tick at least one page.")
                return
            self._timer.stop()
            self._read_ui()
            rows = [r for r in self._rows() if r["use"]]
            self.p["scale"] = rows[0]["scale"]
            _save_params(self.p)
            cmd = self._keep_preview()
            if cmd is None:
                self._undo_preview()
                prog = QProgressDialog("Reading pages…", None, 0,
                                       len(rows), self)
                prog.setWindowTitle(TITLE)
                prog.setWindowModality(Qt.WindowModality.WindowModal)
                prog.setMinimumDuration(0)

                def progress(n, total, page):
                    prog.setValue(n)
                    prog.setLabelText(f"Page {page} ({n + 1} of {total})…")
                    QApplication.processEvents(
                        QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

                try:
                    cmd = self._run(progress, True)
                except Exception as exc:                   # noqa: BLE001
                    prog.close()
                    self._error(f"Import failed:\n{exc}")
                    return
                prog.close()
            try:
                info = cmd.info
                self.viewport.flash_status(
                    f"{TITLE}: {info['sheets']} sheet(s), "
                    f"{info['edges']:,} edges (one undo step)", 6000)
            except Exception:                              # noqa: BLE001
                pass
            self._scan_id += 1                  # drop late worker answers
            super().accept()

        def reject(self):
            if self._computing:
                return
            self._timer.stop()
            self._undo_preview()
            self._scan_id += 1
            self._read_ui()
            _save_params(self.p)
            super().reject()

        def closeEvent(self, ev):
            if self._computing:
                ev.ignore()
                return
            self._timer.stop()
            self._undo_preview()
            self._scan_id += 1
            super().closeEvent(ev)

    return PlanImportDialog


_DIALOG = None
_OPEN = None


def show_dialog(viewport, parent=None):
    """Non-modal, one at a time: the model stays usable while it is open."""
    global _DIALOG, _OPEN
    if _DIALOG is None:
        _DIALOG = _make_dialog_class()
    if _OPEN is not None:
        try:
            if _OPEN.isVisible():
                _OPEN.raise_()
                _OPEN.activateWindow()
                return _OPEN
        except RuntimeError:
            pass
    _OPEN = _DIALOG(viewport, parent or viewport.window())
    _OPEN.show()
    _OPEN.raise_()
    return _OPEN


# ---------------------------------------------------------------------------
# Toolbar (PESI3D): icons drawn in IngeTrazo's own icon style
# ---------------------------------------------------------------------------

def _pesi3d_icons():
    """Icon key → draw(painter, ink, accent) on a 48 px canvas."""
    import math  # noqa: F401
    from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: F401
    from PySide6.QtGui import (QBrush, QColor, QPainterPath, QPen,  # noqa: F401
                               QPolygonF)

    def _a(c, alpha):
        return QColor(c.red(), c.green(), c.blue(), alpha)

    def _dot(p, acc, x, y, r=3.2, color=None):
        p.save()
        p.setPen(Qt.NoPen)
        p.setBrush(color or acc)
        p.drawEllipse(QPointF(x, y), r, r)
        p.restore()

    def _poly(pts):
        return QPolygonF([QPointF(x, y) for x, y in pts])

    def _pencil(p, ink, acc):
        """Small pencil in the lower right corner = «Edit …»."""
        p.save()
        p.translate(35.5, 34.5)
        p.rotate(45)
        pen = QPen(ink, 2.2)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(acc)
        p.drawRect(QRectF(-3.6, -11.0, 7.2, 13.0))
        p.setBrush(QBrush(ink))
        p.drawPolygon(_poly([(-3.6, 2.0), (3.6, 2.0), (0.0, 8.0)]))
        p.restore()

    def plan_import(p, ink, acc):
        # a plan sheet (folded corner) with a floor plan on it, and an
        # arrow taking it into the model
        p.save()
        p.setBrush(Qt.NoBrush)
        p.drawPolygon(_poly([(5, 4), (25, 4), (33, 12), (33, 44), (5, 44)]))
        p.drawPolyline(_poly([(25, 4), (25, 12), (33, 12)]))
        p.restore()
        p.save()                                   # walls of a plan
        p.setPen(Qt.NoPen)
        p.setBrush(_a(acc, 210))
        ring = QPainterPath()
        ring.setFillRule(Qt.OddEvenFill)
        ring.addRect(QRectF(10.0, 17.0, 18.0, 21.0))
        ring.addRect(QRectF(13.0, 20.0, 12.0, 15.0))
        p.drawPath(ring)
        p.drawRect(QRectF(18.0, 20.0, 2.5, 9.0))
        p.restore()
        p.save()                                   # arrow into the model
        pen = QPen(acc, 3.4)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.drawLine(QPointF(30, 30), QPointF(43, 43))
        p.drawPolyline(_poly([(43, 34), (43, 43), (34, 43)]))
        p.restore()

    return {"import": plan_import}


def _pesi3d_toolbar(app, title, entries):
    """A toolbar of this plugin's own — one icon per command (PESI3D).

    ``entries`` = (icon key, text, tip, callable). The icons are drawn
    like IngeTrazo's own (views/icons.py: 48 px, ink = the palette's text
    colour, 3 px pen, the orange accent) and redrawn when the theme flips.
    The toolbar moves, floats and hides like the built-in ones (right-click
    on any toolbar); its place is kept by its objectName."""
    try:
        from PySide6.QtCore import QEvent, QObject, QSize, Qt
        from PySide6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPixmap
        from PySide6.QtWidgets import QApplication, QToolBar
    except Exception:  # noqa: BLE001 — no Qt, no toolbar
        return None
    win = getattr(app, "window", None)
    if win is None:
        return None
    draws = _pesi3d_icons()

    def make_icon(key):
        draw = draws.get(key)
        if draw is None:
            return QIcon()
        qa = QApplication.instance()
        ink = (QColor(qa.palette().windowText().color()) if qa is not None
               else QColor(40, 44, 52))
        pm = QPixmap(48, 48)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing, True)
        pen = QPen(ink, 3.0)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        try:
            draw(p, ink, QColor(243, 115, 41))
        finally:
            p.end()
        return QIcon(pm)

    name = f"pesi3d_{getattr(app, 'key', title)}"
    tb = None
    make = getattr(win, "_new_toolbar", None)     # the host's own builder
    if callable(make):
        try:
            tb = make(title, name)
        except Exception:  # noqa: BLE001
            tb = None
    if tb is None:
        tb = QToolBar(title, win)
        tb.setObjectName(name)
        tb.setMovable(True)
        tb.setFloatable(True)
        try:
            from views.icons import toolbar_icon_px
            px = int(toolbar_icon_px())
        except Exception:  # noqa: BLE001
            px = 24
        tb.setIconSize(QSize(px, px))
        tb.setToolButtonStyle(Qt.ToolButtonIconOnly)
        win.addToolBar(Qt.TopToolBarArea, tb)

    actions = []
    for key, text, tip, fn in entries:
        act = QAction(make_icon(key), text, tb)
        act.setToolTip(f"{text}\n{tip}" if tip else text)
        if tip:
            act.setStatusTip(tip)
        act.triggered.connect(lambda _c=False, f=fn: f())
        tb.addAction(act)
        actions.append((act, key))

    class _ThemeWatch(QObject):
        def eventFilter(self, obj, event):  # noqa: N802 — Qt override
            if event.type() in (QEvent.PaletteChange,
                                QEvent.ApplicationPaletteChange,
                                QEvent.StyleChange):
                for a, k in actions:
                    a.setIcon(make_icon(k))
            return False

    watch = _ThemeWatch(tb)
    tb.installEventFilter(watch)
    tb._pesi3d_watch = watch
    _pesi3d_place_later(win)
    return tb


def _pesi3d_place_later(win):
    """A toolbar the saved window layout does not know yet lands at the end
    of the top row, squeezed behind the built-in ones. Once the window is
    laid out, put new PESI3D toolbars on a row of their own under the
    built-in ones — only the first time each one appears; after that the
    user's own arrangement (saved with the window) wins. Every PESI3D
    plugin carries this code; the first one to get here does it for all."""
    if getattr(win, "_pesi3d_place_pending", False):
        return
    win._pesi3d_place_pending = True
    from PySide6.QtCore import QSettings, Qt, QTimer
    from PySide6.QtWidgets import QToolBar

    def place():
        win._pesi3d_place_pending = False
        try:
            st = QSettings()
            key = "plugins/pesi3d/placed_toolbars"
            placed = st.value(key) or []
            if isinstance(placed, str):
                placed = [placed]
            placed = list(placed)
            bars = [t for t in win.findChildren(QToolBar)
                    if t.objectName().startswith("pesi3d_")]
            new = [t for t in bars if t.objectName() not in placed]
            if not new:
                return
            fresh = not placed            # no PESI3D row yet → open one
            for i, t in enumerate(sorted(new, key=lambda t: t.objectName())):
                shown = not t.isHidden()
                win.removeToolBar(t)
                if fresh and i == 0:
                    win.addToolBarBreak(Qt.TopToolBarArea)
                win.addToolBar(Qt.TopToolBarArea, t)
                t.setVisible(shown)
            st.setValue(key, placed + [t.objectName() for t in new])
        except Exception:  # noqa: BLE001 — layout only, never break the app
            pass

    QTimer.singleShot(0, place)


def setup(app) -> None:
    app.add_menu_action(
        f"{TITLE}…", lambda: show_dialog(app.viewport, app.window),
        tip="Import a floor plan or a whole plan set from a vector PDF as "
            "clean edges.")
    _pesi3d_toolbar(app, TITLE, [
        ("import", f"{TITLE}…",
         "Import floor plans or a whole plan set from a vector PDF as "
         "clean edges at their scale.",
         lambda: show_dialog(app.viewport, app.window)),
    ])
