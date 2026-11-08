# Performance on large plan sets

On a big plan set, Plan Import 1.2.1 froze IngeTrazo for minutes. Live Preview took 90 s with the window frozen, and every option change cost about a minute more. This page records what was slow, what changed, and the measurements against 1.2.1. Every import result is unchanged: the same edges, texts and style records.

## The test material

A 9-sheet architectural plan set plotted from Revit: general-arrangement floor plans of a multi-storey building, basement to roof. The file itself is a client's drawing set, so it is not in the repository; only its make-up is given here.

| | |
|---|---|
| Sheets | 5 × A0 (3370 × 2384 pt) and 4 × A1 (2384 × 1684 pt), all at 1:100 |
| File | 15.5 MB, content streams Flate-compressed |
| Decoded content streams | 72 MB in all. Per sheet: 0.8 · 29.5 · 10.4 · 5.4 · 10.2 · 3.9 · 3.9 · 6.1 · 1.7 MB |
| Painted paths | 1.26 M; the largest sheet 554,451 |
| Straight segments | 1.73 M; the largest sheet 701,986 |
| Points | 3.0 M |
| Make-up | Almost all single-segment stroked paths («x y m x y l S»). 116–1,927 filled shapes and 150–560 texts per sheet. Marked content (BMC/EMC). No Form XObjects, images or curves. |
| Fine detail | 82 % of the largest sheet's paths are under 0.2 mm on paper (dots and dashes of line patterns); the median segment is 0.1 mm long |
| Imported with the defaults | ≈ 1.06 M edges in 9 groups |

The four PDFs in `examples/` were checked as well. They are tiny, so they test equality, not speed.

## How it was measured

- **Equality.** Before any change, 1.2.1's output was hashed at every stage, for every page of the plan set and of `examples/*.pdf`:
  - `extract` (every path);
  - `page_texts` (name, scale and every text);
  - `collect_runs` and `dedupe_overlaps`;
  - `close_gaps` at 1, 5, 20 and 100 mm;
  - `style_record`, with and without a crop.

  With the changes, all hashes match: 0 mismatches.
- **The plain-Python stages** ran on CPython 3.13.12, Windows 11, on a 20-thread desktop. Times are wall-clock seconds; repeated runs vary by about ±10 %.
- **The page view and the dialog** ran on Python 3.12 with PySide6 6.11.2, as shipped with IngeTrazo 0.6, offscreen.
  - The dialog was driven through the same session with 1.2.1 and with this version.
  - IngeTrazo's `core` was replaced by a stand-in that welds points and lists edges in Python, much as IngeTrazo does. So the mesh part of these times is indicative; everything else is the plugin's own code.
  - **Freeze** means the longest time the event loop did not run, which the user sees as a frozen window.
- **Not measured:** IngeTrazo itself (its mesh objects, viewport and saving), and macOS or Linux.

## The dialog, same session

| Step | 1.2.1 time | 1.2.1 freeze | Now: time | Now: freeze |
|---|---|---|---|---|
| Largest sheet clicked right after opening | 50.7 s | **50.5 s** | 5.4 s | 3.2 s |
| Live Preview on → preview shown | 90.6 s | **90.6 s** | 26.7 s | 1.1 s |
| «Close gaps» changed → new preview | 52.6 s | **52.2 s** | 26.5 s | 1.1 s |
| One floor plan's elevation changed → preview | — | — | 1.7 s | 0.4 s |
| OK | 0.0 s | — | 6.4 s ¹ | — (progress dialog) |
| Result | 9 groups, 1,061,221 edges | | the same | |

¹ In 1.2.1 the style records (line styles for the Walls plugin) were made during the 90-second freeze. Now they are made in the background after the preview appears, and OK waits for whatever is left behind a progress dialog.

## Reading the sheets

Each page used to be tokenised twice by pypdf, about 3.5 MB/s: once for its texts and once for its lines. The texts are now read from the text objects alone, and the lines with one regular expression over the decoded stream. Anything unusual falls back to pypdf's own route (`content_stream.py`).

Measured in one process, texts and geometry together:

| Sheet | Format | Content stream | Paths | Segments | 1.2.1 | Now |
|---|---|---|---|---|---|---|
| 1 | A0 | 0.8 MB | 12,343 | 21,277 | 0.52 s | 0.07 s |
| 2 | A0 | 29.5 MB | 554,451 | 701,986 | 22.24 s | 1.64 s |
| 3 | A0 | 10.4 MB | 177,267 | 258,073 | 7.19 s | 0.78 s |
| 4 | A0 | 5.4 MB | 80,264 | 145,633 | 3.92 s | 0.36 s |
| 5 | A0 | 10.2 MB | 176,889 | 228,546 | 8.42 s | 0.64 s |
| 6 | A1 | 3.9 MB | 60,120 | 99,440 | 2.86 s | 0.31 s |
| 7 | A1 | 3.9 MB | 62,532 | 96,140 | 2.72 s | 0.25 s |
| 8 | A1 | 6.1 MB | 108,159 | 139,801 | 4.34 s | 0.40 s |
| 9 | A1 | 1.7 MB | 30,345 | 37,624 | 1.12 s | 0.11 s |
| **All** | | **72 MB** | **1,262,370** | **1,728,520** | **53.3 s** | **4.6 s** |

Split by stage, all sheets: texts **23.4 s → 0.8 s**; geometry **26.9 s → 3.8 s**.

## Clean up

«Close gaps» looked at every segment in the 3 × 3 block of 12 pt cells round each loose end. For the default gap (5 mm real = 0.14 pt on the sheet), that meant about 4,750 candidates per loose end on the largest sheet. It now searches cells sized to the gap and gives the same result, ties included.

| «Close gaps», all 9 sheets | 1.2.1 | Now |
|---|---|---|
| 1 mm | 292.2 s | 15.7 s |
| 5 mm (default) | 64.3 s | 13.8 s |
| 20 mm | 26.3 s | 15.6 s |
| 100 mm | 50.0 s | 18.3 s |

| Sheet | 5 mm: 1.2.1 | 5 mm: now | 1 mm: 1.2.1 | 1 mm: now |
|---|---|---|---|---|
| 1 | 0.19 s | 0.22 s | 0.22 s | 0.18 s |
| 2 | 41.55 s | 3.77 s | 259.32 s | 4.70 s |
| 3 | 5.75 s | 2.27 s | 9.32 s | 2.95 s |
| 4 | 2.96 s | 1.61 s | 3.27 s | 1.53 s |
| 5 | 6.10 s | 2.33 s | 10.97 s | 2.41 s |
| 6 | 1.98 s | 0.86 s | 2.03 s | 1.11 s |
| 7 | 1.82 s | 0.94 s | 2.00 s | 0.95 s |
| 8 | 3.29 s | 1.34 s | 4.17 s | 1.27 s |
| 9 | 0.66 s | 0.41 s | 0.90 s | 0.55 s |

The tables above were measured without the GC pause. With it, as the dialog runs them, `tools/bench.py` gives these for all sheets at 5 mm:

| Stage | Time now | Note |
|---|---|---|
| Filters | 2.4 s | |
| «Merge duplicates» | 3.7 s | 4.6 s in 1.2.1 |
| «Close gaps» | 11.8 s | |
| Style records | 8.3 s | 9.6 s in 1.2.1. Now made once per sheet, crop and options, in the background, and not for the preview |

## Page view

Paths that follow each other and use the same pen are now drawn as one painter path, built in a single call. The largest sheet needs 1,942 of these batches and 552 fills instead of 554,451 separate paths. The drawing order stays the PDF's. At most 0.008 % of the pixels differ by more than 8 of 255 levels, none by more than 64; the differences come from antialiasing where strokes overlap. All four sheets below are drawn at 4096 px.

| Sheet | Paths | 1.2.1 | Now |
|---|---|---|---|
| 2 (A0) | 554,451 | 2.60 s | 0.67 s |
| 3 (A0) | 177,267 | 0.85 s | 0.32 s |
| 5 (A0) | 176,889 | 0.84 s | 0.29 s |
| 9 (A1) | 30,345 | 0.17 s | 0.10 s |

## Memory and IngeTrazo

- **GC.** With the 9 sheets read, about 5 M objects are tracked by the garbage collector, and a full collection takes 0.4–0.6 s. The heavy loops now run with the cyclic GC paused, as IngeTrazo's own importers do. Reading the largest sheet takes 1.9 s with GC and 1.1 s without.
- **Caches.** 1.2.1 kept the runs of every option combination ever tried and never let them go: about 145 MB each on this set after «Merge duplicates», more with «Close gaps». Now only the current and the previous options are kept.
- **Layers.** Each sheet's group is labelled before it is inserted, and one command creates the missing layers. The `AssignLayerCommand` per group in 1.2.1 made IngeTrazo re-check every group in the document, twice on each preview update.

## What is left

- **About 18 s of background clean-up** per change of a content option (close gaps 11.8 s, merge 3.7 s, filters 2.4 s), plus 8.3 s of style records. These are candidates for numpy.
- **IngeTrazo's mesh construction**, one sheet per turn of the event loop. With the stand-in this is about 1.1 s for the largest sheet; real IngeTrazo is not measured. An array-based bulk edge API in IngeTrazo would remove it.
- **A freeze of about 3 s** when the largest sheet is shown the moment it has been read. This is the collection of its freshly made objects plus the drawing.
- **Native code (Rust) is not needed for any of the above.** All PDF reading together is now 4.6 s, in the background.

## Reproduce

```
python tools/bench.py PLAN.pdf [--gap MM] [--scale N] [--plain]
python -m unittest discover -s tests
```

- `--plain` also runs pypdf's route on each sheet and reports any difference.
- The tests compare the faster code with the 1.2.1 code, which is kept in `tests/_reference.py` and `pdf_vectors._walk_ops`.
- The dialog session was driven by an offscreen script with a stand-in for IngeTrazo's `core`. It is not part of the repository because it depends on IngeTrazo internals.
