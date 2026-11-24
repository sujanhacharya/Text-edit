# InkPatch continuation — 2026-11-17 checkpoint

## Table of contents

- [Status at a glance](#status-at-a-glance)
- [Environment and rules](#environment-and-rules)
- [Verification commands](#verification-commands)
- [What was completed in this pass](#what-was-completed-in-this-pass)
- [Visual verification already done](#visual-verification-already-done)
- [Open defects and next tasks (in order)](#open-defects-and-next-tasks-in-order)
- [Regression contract — do not break these](#regression-contract--do-not-break-these)
- [Blocked here (needs the user's environment)](#blocked-here-needs-the-users-environment)
- [Useful paths](#useful-paths)

Read `PROMPT.md` first; it is the canonical instruction and risk log. This file
is the short, task-oriented handoff for resuming the paused OCR/render-quality
pass. The project is **not a git repository**: every change is already saved in
the working tree.

> **2026-11-19:** the frontend-only visual refresh is complete; see the design
> entry in `PROMPT.md`. Every root Markdown document now has a generated table
> of contents, and failed frontend requests share one persistent **Retry all**
> notice instead of retrying each selection. The MinTest `.venv` described below
> is not installed in the current session, so the full test/OCR commands need
> the user's `./mintest.sh` run first. The static UI and docs-TOC tests pass; the
> 225-test suite has seven NumPy-stub OCR errors and the pre-existing
> `install.sh` executable-bit failure.

## Status at a glance

- At the 2026-11-17 checkpoint, the automated suite passed: **206 tests, OK**,
  `compileall` clean.
- The OCR selection overhaul is complete: row-span safety, graphic/border
  filters, slope-aware fragment rejoining, covered-tail removal, document-wide
  mono-family stabilization, and conservative size estimation.
- The full `typewritten` corpus group passes (`0 fail`), but visual inspection
  is only done for the original, `v_lowcontrast`, and `v_photo`.
- Next defect: mono/all-caps point-size drift on `typewritten.jpg` (stored
  19-25 px, pitch implies 23.3-24.7 px).
- Then: inspect the five remaining typewritten stacks, investigate
  `v_strike` (only 11 lines), run the other 11 corpus groups, and exercise
  style/fill/new-box renders.

## Environment and rules

```bash
cd /home/lubuntu/textedit
. .mintest.env && . .venv/bin/activate
```

Installed at the checkpoint: Pillow 12.3.0, NumPy 2.5.5, OpenCV 4.14.0,
Pydantic 2.14.3, Tesseract 5.5.0 with `eng`. **FastAPI, uvicorn, and Node.js
are absent.**
Firefox/Selenium exist but must not be started headlessly without user
approval; browser runs have caused lag on this machine.

- Do not install anything with `pip`/`apt`; the user does that.
- Do not add Python dependencies, a build step, a CDN, or telemetry.
- Do not touch the file named `New file` in the project root.
- Do not add a document listing/gallery or expose tokens.

## Verification commands

```bash
. .mintest.env && . .venv/bin/activate
python3 -m unittest discover -s tests -v
python3 -m compileall -q app scripts tests
```

Corpus groups are audited one slug at a time. `check_corpus.py` overwrites
`testdata/report.json`, so keep a separate `--data` directory per run:

```bash
GROUP=sign_photo
rm -rf /tmp/opencode/corpus/$GROUP
mkdir -p /tmp/opencode/corpus/$GROUP
python3 scripts/check_corpus.py --slug "$GROUP" --keep \
  --data /tmp/opencode/corpus/$GROUP \
  > /tmp/opencode/corpus/$GROUP.log 2>&1
tail -8 /tmp/opencode/corpus/$GROUP.log
python3 /tmp/opencode/inspect_edits.py \
  /tmp/opencode/corpus/$GROUP \
  /tmp/opencode/corpus/${GROUP}_insp 1.5
```

`inspect_edits.py` builds a source/preview stack and a per-line comparison
sheet for every document. Open the generated `*_stack.png` and `*_lines.png`.
If the scratch script is gone after a reboot, it only needs Pillow/NumPy and
can be recreated from the same idea: stack `image.png` over `preview.png` and
crop each line box for a closer view.

## What was completed in this pass

### `app/ocr.py`

- Baseline-aware fragment grouping; `_line_baseline()`,
  `_usable_recovered_line()`, `_trim_edge_noise()`.
- Isolated-glyph filtering and `_plausible_line()` floors for weak fragments.
- `_split_multibaseline_lines()` keeps one box from containing two rows.
- `_has_oversized_glyph_cells()` and `filter_graphic_lines()` reject icon
  grids/textures on weak-confidence lines only.
- Projection-profile skew estimation, preferring a clear projection peak over
  Hough; fixes the wrong `-1.9°` estimate on the warped `v_photo` page.
- `_line_spans_rows()` and `_resolve_overlapping_lines()` post-merge safety.
- `filter_border_clipped_lines()` public wrapper; `_border_clipped_line()`
  handles both a clipped oversized title and a clipped shorter body row.
- Fragment reconstruction groups by median word height, treats row-spanning
  fragments as barriers, and drops isolated tails.

### `app/imaging.py`

- `_row_adjusted_baseline_delta()` removes the page row slope before comparing
  fragment baselines, rejoining warped same-row pieces without gluing the next
  row. `_segments_mergeable()` uses
  `max(4.0, shorter * 0.50)` and falls back to raw medians with fewer than three
  word centres.
- `_drop_covered_segments()` removes a tail whose box is at least 60% covered by
  a wider same-row line.
- Font size uses stable text width for long lines when width implies a smaller
  size than the noisy tight-mask height; ink height stays authoritative when
  width implies a larger size.
- Rejoined fragments re-estimate size from median word height plus union width.
- `stabilize_monospaced_document()` now unifies low-confidence lines and
  moderate-confidence non-mono outliers on the dominant mono family, preserves
  confident proportional headings (font confidence >= 40), and leaves
  confident mono lines' weight/slant/size untouched (>= 15).

### `app/storage.py`

- Pipeline order is now: per-line style sampling -> `split_styled_lines()` ->
  re-sample new segments -> **`filter_border_clipped_lines()`** ->
  `merge_compatible_segments()` -> `filter_graphic_lines()` ->
  `stabilize_typefaces()` / `stabilize_monospaced_document()` ->
  `infer_alignment()`.
- The clipped-row filter must stay **before** merging: a clipped bottom row can
  share a baseline with the complete row above and would otherwise be glued to
  it.

### Tests

New regressions cover clipped-word span detection, row-spanning fragment
barriers, tightly spaced rows kept, duplicate same-row readings dropped,
clipped top/bottom border rows, complete border rows kept, covered tails
dropped, low-confidence mono unification, warped same-row rejoins, next-row
tall-box rejoin rejection, and confident proportional headings surviving a
mono page. Total: **206**.

## Visual verification already done

Runs live under `/tmp/opencode/corpus/`:

- `typewritten2/` + `typewritten2_insp/` — full group, 8 files, 0 fails,
  134 warnings.
- `tw_lc/` + `tw_lc_insp/` — low-contrast variant after the mono fix.
- `run_tw9/` + `insp_tw9/` — warped `v_photo` after the row-safety fix.

Inspected stacks and findings:

| Variant | Folder | Lines | Visual result |
| --- | --- | ---: | --- |
| original | `ade064b6` | 21 | coherent rows; some OCR text artifacts and point-size drift |
| `v_lowcontrast` | `651152dd` | 20 | consistent mono face; acceptable |
| `v_photo` | `4083189a` | 16 | no cross-row overprint; some source ghosting accepted because the photo is cropped/warped |
| `v_underline` | `762e09e2` | 25 | stack generated, **not yet inspected** |
| `v_strike` | `e05d5380` | 11 | **not inspected**; line count is suspiciously low |
| `v_small` | `c9426186` | 24 | stack generated, **not yet inspected** |
| `v_rot3` | `79072bc8` | 19 | stack generated, **not yet inspected** |
| `v_vignette` | `55712e2f` | 25 | stack generated, **not yet inspected** |

Accepted degradation: `typewritten__v_photo.jpg` is cropped and perspective-
warped; the top half is missing, so no OCR pass can read it fully. The goal is
coherent rows, no cross-row overprinting, and no huge false selections.

## Open defects and next tasks (in order)

### 1. Fix mono/all-caps point-size drift

Evidence on `typewritten.jpg` (folder `ade064b6`):

- character pitch is stable at about `14.0-14.75 px` per character;
- stored `font_size` is `19-25`, while the mono advance implies
  `23.3-24.5` for every line;
- all-caps lines `L007`/`L011` render slightly oversized in the preview.

Plan:

- In `stabilize_monospaced_document()` (`app/imaging.py`), the pitch loop
  currently divides word width by the count of **alphanumeric** characters and
  skips words with fewer than three. This undercounts punctuation and makes
  short/all-caps words noisy.
- Count **all non-space characters** for pitch (`len(re.sub(r"\s+", "", text))`)
  and keep a minimum sample count.
- Use the page's median mono pitch to derive one expected advance. For a mono
  face, `advance_at_96 = font.getlength("M")`, so
  `expected_size = 96 * median_pitch / advance_at_96`.
- Apply the pitch-derived size to mono lines when the line has enough pitch
  samples, instead of the noisy height/width estimate. Keep a sanity range and
  do not override a clearly different heading size.
- Add a regression with punctuation-heavy words and an all-caps line; assert
  the sizes unify near the pitch estimate.
- Re-render `typewritten.jpg` and `v_lowcontrast`, then inspect both stacks.

### 2. Inspect the five remaining typewritten stacks

Open:

```
/tmp/opencode/corpus/typewritten2_insp/762e09e2_stack.png   # underline
/tmp/opencode/corpus/typewritten2_insp/e05d5380_stack.png   # strike
/tmp/opencode/corpus/typewritten2_insp/c9426186_stack.png   # small
/tmp/opencode/corpus/typewritten2_insp/79072bc8_stack.png   # rot3
/tmp/opencode/corpus/typewritten2_insp/55712e2f_stack.png   # vignette
```

For each, check: ghost/double glyphs, leftover source text, wrong row merges,
clipped or overlapping replacements, family/size/alignment drift, and fills.
`v_strike` (`e05d5380`, 11 lines, ~17k changed pixels outside line boxes) is
the highest priority: find out whether the drawn strike bands caused lines to
be rejected by `filter_graphic_lines()` or merged with neighbours.

### 3. Run the remaining corpus groups

Each group has its own `--data` directory. Inspect every stack before moving on.

| Group | Files |
| --- | ---: |
| `sign_photo` | 7 |
| `menu_columns` | 8 |
| `newspaper_ad` | 8 |
| `poster_centre` | 7 |
| `poster_army` | 7 |
| `pricelist` | 7 |
| `bookcover` | 4 |
| `label_photo` | 7 |
| `photo_fog` | 4 |
| `photo_forest` | 4 |
| `photo_texture` | 4 |
| `screenshot_ui` (re-check) | 7 |

`screenshot_ui` must be re-checked because the new border/graphic filters can
affect small UI text. The 2026-11-16 baseline was 0 fails / 123 warnings; a
change in line count is not automatically a regression, but any dropped real
label is.

### 4. Exercise style, fill, new-box, and copy-style renders

Use `store.create()` + `store.render()` directly; FastAPI is not needed.
`RenderEdit` fields live in `app/schemas.py`.

```python
import sys
from pathlib import Path
sys.path.insert(0, ".")
import app.storage as S
from app.schemas import RenderEdit

src = Path("testdata/corpus/typewritten.jpg")
doc = S.store.create(src.read_bytes(), src.name, source="corpus")
lines = doc["lines"]
ids = [line["id"] for line in lines]

edits = [
    RenderEdit(line_id=ids[1], text=lines[1]["text"] + " EDITED"),
    RenderEdit(line_id=ids[2], text=lines[2]["text"], underline=True),
    RenderEdit(line_id=ids[3], text=lines[3]["text"], strikethrough=True),
    RenderEdit(line_id=ids[4], text=lines[4]["text"], shadow=True),
    RenderEdit(line_id=ids[5], text="", background="#f2c94c"),
    RenderEdit(
        line_id="N001",
        text="New text box",
        box={"x": 120, "y": 460, "width": 320, "height": 44},
        font_size=28,
        background="#cfe8ff",
    ),
]
print(S.store.render(doc["id"], edits))
print(S.store._dir(doc["id"]) / "preview.png")   # inspect this file
```

Inspect at readable zoom:

- underline/strike/shadow affect only the edited line and use the original
  colour;
- no synthetic shadow appears without `shadow=True`;
- the fill stays inside the canvas, follows grown text, and never covers
  another line/new box;
- the new box is drawn over the source (originals still visible elsewhere, not
  erased), starts without an accidental background;
- copy-style is a frontend payload operation: verify the copied fields exclude
  `background` and that the rendered result matches family/size/weight/slant/
  colour/alignment/effects but not the source fill.

### 5. Finish the docs and fold the temporary instruction

- `PROMPT.md` now has the 2026-11-17 checkpoint, implementation bullets,
  risk 21, and the 206-test count. Keep it current as tasks close.
- `TESTING.md` and `MinTest.md` counts were updated to 206.
- `README-dev.md`, `ARCHITECTURE.md`, and `README.md` may still mention the old
  filter/pipeline order; check them for stale claims.
- The temporary blockquote at the top of `PROMPT.md` ("Following a new user
  instruction") must be folded into the canonical instruction log and deleted
  once the remaining work is done, per `AGENTS.md`.

## Regression contract — do not break these

These decisions were expensive to get right. Re-run the relevant tests after
touching the filters or merge code.

- `filter_graphic_lines()` only applies to weak-confidence lines. Confident text
  with a coloured background or a drop cap must survive.
- `_border_clipped_line()` requires **all** of: border contact, width < 70% of
  the image, >= 2 words, confidence < 80, a dense ink band on the border, and a
  height either `>= 1.35x` (title) or `<= 0.90x` (body) the page median. A
  complete line sitting at the edge must be kept.
- `_line_spans_rows()` rejects only when at least two words are taller than
  `1.8x` the median word height.
- `_resolve_overlapping_lines()` requires the same baseline **and** overlapping
  word masks; tightly spaced adjacent rows with touching line boxes stay.
- `_row_adjusted_baseline_delta()` uses a shared fitted slope and returns
  `None` below three word centres; threshold is
  `max(4.0, shorter * 0.50)`.
- `_drop_covered_segments()` uses a 60% coverage threshold.
- `stabilize_monospaced_document()` thresholds: >= 20 pitch samples, pitch
  CV <= 0.14, median pitch/height 0.60-0.92, lowercase letters >= 55%. Confident
  proportional headings (>= 40) survive; confident mono lines (>= 15) keep
  weight/slant/size.
- The demo poster intentionally has no shadows. Never invent one automatically.
- Every style override and fill is local to the selected line/new box. A fill
  may follow grown text but must never cover another selection box.
- New boxes are drawn over the source and must never enter the removal mask.
- Copy style never copies the background.

## Blocked here (needs the user's environment)

- FastAPI/uvicorn are absent: live API, cookie, TTL/cleanup, and browser checks
  cannot run.
- Node.js is absent: `node --check app/static/app.js` cannot run.
- Headless Firefox/Selenium need explicit user approval; prefer static checks
  and manual browser verification.
- No `pip`/`apt` installs by the agent.

## Useful paths

- `app/ocr.py` — OCR assembly, recovered lines, filters, skew.
- `app/imaging.py` — mask/style/render pipeline, merge safety, mono sizing.
- `app/storage.py` — OCR pipeline order and `store.render()`.
- `tests/test_ocr_selection.py`, `tests/test_imaging_selection.py` — regressions.
- `scripts/check_corpus.py` — full-pipeline corpus audit.
- `testdata/manifest.json` — slug mapping; `--slug` matches the manifest slug.
- `/tmp/opencode/inspect_edits.py` — stack/comparison sheet generator.
- `/tmp/opencode/corpus/typewritten2/` — latest full group run.
- `PROMPT.md` — canonical instructions, risks, and required checks.
