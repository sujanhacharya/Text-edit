# InkPatch

## Table of contents

- [Features](#features)
- [How style preservation works](#how-style-preservation-works)
- [Requirements](#requirements)
- [Guided installer](#guided-installer)
- [Manual quick start](#manual-quick-start)
- [Configuration](#configuration)
- [API](#api)
- [Project layout](#project-layout)
- [Privacy and operational notes](#privacy-and-operational-notes)
- [Limits](#limits)
- [Third-party assets](#third-party-assets)

InkPatch is a local web app for editing text inside an existing image. It runs
Tesseract OCR, lets you change individual lines, removes the old glyphs, and
redraws the new text using the typography and colour it detected in the source.
The rest of the image is left alone.

It is designed for posters, menus, screenshots, slides, memes, scanned pages,
and photos of printed or handwritten notes. It is not a generative image
editor: no diffusion model is used and no GPU is required. In its default local
deployment no image is sent to a third party; see [HOSTING.md](HOSTING.md) for
the privacy and security changes that apply when you host it remotely.

## Features

- **Local OCR** through the Tesseract command line, with installed language
  packs discovered automatically.
- **Photo preprocessing.** A photographed page is perspective-corrected and
  deskewed into the stored working image, illumination is flattened, and
  normal/inverted/threshold passes are scored so light-on-dark signs and uneven
  phone photos have a much better chance of being read.
- **Unicode-aware OCR filtering.** Letters, marks, numbers, punctuation, and
  textual symbols are kept inside a line — including `©`, `®`, `™`, `°`, `‰`,
  `₹`, arrows, mathematical signs, every currency sign, and split ellipsis
  tokens.
  Symbol-only lines are editable when they are real text; borders, repeated
  rules, standalone list markers, emoji-only rows, and icon/ornament blocks are
  ignored. Browser/OS icons beside real words start a new selection, so editing
  the words cannot erase the icon or replace it with a guessed character.
  Icons and emoji that cannot be drawn faithfully are left as original pixels;
  InkPatch never invents a random symbol for them.
- **Full-line recovery.** When Tesseract's page segmentation misses or truncates
  a line, OpenCV finds the ink row and Tesseract re-reads that line on its own.
  Lines from the automatic, sparse, and thresholded passes are merged so a
  single high-confidence word cannot hide the rest of the line.
- **Fragment stitching.** Sparse-mode fragments on the same baseline are joined
  into one visual line in reading order before style sampling. Ordinary word
  spacing and tracked headings merge, while table columns, tabs, and isolated
  icons keep their gaps. Split URLs are rejoined (including a missing dot),
  unmatched icon prefixes and tiny toolbar glyphs are dropped, and overlapping
  fragment boxes cannot glue words together (`Attendance Hub`, not
  `AttendanceHub`).
- **RTL-aware line assembly.** Arabic and Hebrew lines keep Tesseract's reading
  order instead of being re-sorted left-to-right; mixed-direction lines use a
  majority check.
- **Style inheritance by default.** Each line keeps its sampled typeface,
  weight, slant, size, colour, alignment, underline/strikethrough, and original
  drop shadow; when a line is edited, the background is repaired around the new
  glyphs before the replacement is drawn.
- **Multi-font matching.** Bundled DejaVu, Liberation, and Noto Latin faces are
  compared against the source glyphs, and common system fonts are discovered
  too. The specific detected family is stored with each line.
- **Opt-in overrides** for colour, a concrete font family, weight/style,
  alignment, size, underline, strikethrough, shadow, and a per-line background
  fill.
- **New text boxes.** Press **Add text**, then click the image to place a box.
  Type the new text and choose its font independently, or use **Copy style** to
  copy the typeface, size, weight, slant, colour, alignment, and effects from
  another selection. Copying a style never copies the background colour.
- **Per-line background fill.** Fill the selected line or new text box with any
  colour, or mix it toward black or white with a **Dark ↔ Light** slider. The
  inspector shows the background colour detected in the source. The fill follows
  replacement text when it grows, but is clipped to the canvas and split around
  every other detected or new selection box, so it never paints over another
  line. It is applied before replacement text, so it cannot hide another edited
  line's new glyphs. Remove the line's text first if the goal is to cover it
  completely.
- **Shadow and halo handling.** Old text and its halo are removed first (flat
  backgrounds get a clean fill, textured backgrounds a median estimate, and
  busy photos content-aware inpainting). A genuine original drop shadow is
  detected conservatively and redrawn behind the replacement text; clean text
  does not gain a synthetic shadow unless the line's **Shadow** effect is
  explicitly switched to **On** (a detected shadow is kept instead of adding a
  second one).
- **Style-aware selections.** A Tesseract line is split when an outlier gap,
  icon, colour change, size change, underline/strikethrough change, or
  different family/weight/slant separates two word groups. Ordinary
  wide-spaced prose and evenly tracked headings stay as one editable line;
  table columns and mixed-style labels become separate selections.
- **Focused editing** with line search and filters, per-line restore, document
  rescan, zoom/pan, and hit detection on the image overlay. A one-click **Dot
  all** action appends a full stop to every detected line and renders once, so
  selection and rendering problems can be checked quickly.
- **Graceful retry.** If the server does not respond, the latest edits stay in
  the editor. One **Retry all** action resends the pending selection changes in
  a single request instead of retrying every keystroke or line; a dismiss button
  clears the notice, and a failed retry brings it back.
- **Clipboard export.** Copy the selected line's current text, including edits,
  or copy all current in-image text in reading order with one click.
- **Responsive editor.** The same UI works from small and flip phones through
  tablets, laptops, and desktops. The landing view stacks to one column at
  `≤1020px`; at `≤1100px` the editor becomes a full-width canvas with the text
  line list and inspector as slide-in drawers. Primary touch buttons are
  38px, safe-area padding handles notches and home indicators, and short
  landscape screens scroll instead of squeezing the canvas.
- **Live preview** rendered by the server; only changed lines are sent on each
  render request.
- **PNG export**, plus a built-in demo document.
- **No frontend build step, CDN, analytics, or database.** The UI is plain
  HTML, CSS, and ES2017-compatible JavaScript served by FastAPI.
- **Per-document browser cookies.** Each upload or demo receives its own random,
  `HttpOnly` capability cookie scoped to that document's API path. There is no
  account system and no document listing.
- **Filesystem storage.** Each document is a folder under `data/`. A folder that
  has not changed for four days is deleted, including its image, preview, and
  metadata.

## How style preservation works

When OCR runs, InkPatch samples every detected line:

| Attribute | Detection method |
| --- | --- |
| Foreground colour | Median colour of pixels that differ from the surrounding background |
| Background | Median of a ring sampled outside the tight OCR box; shown in the inspector and used as the starting colour for a per-line fill |
| Typeface | The OCR line is rasterized in several sans, serif, and mono families and compared with a solid-ink glyph mask that excludes antialiasing and drop shadows |
| Weight and slant | The winning family's regular, bold, italic, and bold-italic faces are compared, even when two families score closely |
| Size | Estimated from the height of the original solid ink, with a width sanity check, relative to the chosen font; kept through edits unless overridden |
| Alignment | Inferred from the document's left/right margins and centre line; ambiguous lines stay left-aligned |
| Underline / strikethrough | A continuous solid rule below or through the glyphs, including an underline that skips around a descender; short words are ignored when the rule could be a glyph crossbar. Redrawn with the replacement text |
| Drop shadow | Detected on the first edit from a directional, offset copy of the solid glyph core; cached with the line style. Clean lines stay shadow-free unless **Shadow → On** is chosen |

For Latin text, the bundled DejaVu, Liberation, and Noto faces are used for
matching and rendering; common system fonts are discovered as additional
candidates. When a short line loses a close family race, it inherits the
dominant family already found in the same sans/serif/mono category instead of
switching to an unrelated face; confident matches are left alone. For CJK,
Arabic, Hebrew, Devanagari, and Kannada text, InkPatch looks for suitable Noto
system fonts and uses the original script when drawing. The detected values are
stored with the document.

Rendering is inheritance-first:

- `font: "auto"` and `font_family: null` keep the detected family.
- `bold: null` and `italic: null` keep the detected weight and slant.
- `color: null` keeps the sampled colour.
- `scale: 1.0` keeps the detected size.
- `font_size: null` keeps the detected size; for a new box it defaults from the
  box height. A number sets an absolute base size in working-image pixels, and
  `scale` still applies on top of it.
- `align: "auto"` keeps the detected alignment; `left`, `center`, and `right`
  are explicit overrides.
- `underline: null`, `strikethrough: null`, and `shadow: null` keep the
  detected decoration/effect; `true` forces it on and `false` removes it. An
  explicit `shadow: true` keeps a detected shadow, or adds a subtle drop shadow
  when the source line had none.
- `background: null` keeps the repaired original background; a six-digit hex
  string fills the selected box and follows the replacement glyphs where they
  grow into free space, without covering another selection.
- A new box is sent with a `box` rectangle and no source line. It is drawn over
  the existing pixels instead of erasing an OCR line; copying a style into it
  uses the same edit fields as an existing line.

Only lines in the request are repainted. For photos, the stored working image
is straightened at upload so OCR boxes and rendered text share the same
coordinate space. The preview and download are generated from a fresh copy;
rendering never overwrites the working image.

## Requirements

- Python 3.10 or newer
- Tesseract 4 or newer
- A current Chrome, Firefox, Safari, or Edge
- No Node.js, GPU, database, or cloud account for the default local deployment

See [INSTALL.md](INSTALL.md) for Fedora/RHEL, Ubuntu/Lubuntu, Arch, openSUSE,
Alpine, FreeBSD/DragonFly, OpenBSD, NetBSD, macOS, Windows, Android/Termux, and
Docker/Podman commands.

## Guided installer

On Linux, BSD, or macOS:

```bash
./install.sh
```

On Windows, run `install.bat` from Command Prompt or PowerShell.

The installer prints an ASCII InkPatch logo and uses terminal colour when run on
an interactive terminal. Set `NO_COLOR=1` (or use `TERM=dumb`) to disable colour.
It detects Debian/Ubuntu, Fedora/RHEL, Arch/Manjaro, openSUSE/SLES, Alpine,
FreeBSD/DragonFly, OpenBSD, NetBSD, and macOS, then checks Python 3.10+, the
Python `venv` module, Tesseract with English (`eng`) data, and the OpenCV
runtime. It creates `.venv` and installs `requirements.txt` automatically. If
Python, Tesseract, or a required system library is missing, it prints the
matching `apt`, `dnf`, `pacman`, `zypper`, `apk`, `pkg`/`pkg_add`, `brew`, or
`winget` command instead of running it itself. When Docker or Podman is already
installed, it asks whether to use the container runtime or the native OS
install. With neither runtime installed, it prints platform-specific
Docker/Podman installation commands and continues with the native install. A
container choice prints the Compose or direct build/run commands, including
optional `TESSERACT_LANGS` and `EXTRA_FONTS` build arguments, without building
anything. Android/Termux is not modified automatically; the installer prints the
Termux packages and manual venv commands first. Other unsupported systems exit
with a pointer to [INSTALL.md](INSTALL.md). At the end it prints the command to
start InkPatch.

It also checks optional OCR language data (`deu`, `chi_sim`, `chi_tra`, `hin`,
and `kan`) and the Noto CJK, Devanagari, and Kannada fonts. If any are missing it
prints the matching package-manager or font instructions; optional packages do
not block startup.

## Manual quick start

Install Tesseract for your platform first, then:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>.

On a BSD base shell or Alpine's BusyBox shell, activate the environment with
`. .venv/bin/activate` instead of `source .venv/bin/activate`.

The OCR language list comes from the `.traineddata` files installed with
Tesseract. English (`eng`) is enough to start; install additional language packs
for other languages and reload the page. [INSTALL.md](INSTALL.md) lists the
Chinese (`chi_sim`/`chi_tra`), Hindi (`hin`), and Kannada (`kan`) packages and
font notes for each platform.

### Docker

```bash
docker compose up --build -d
```

Open <http://127.0.0.1:8000>. The Compose file publishes the port on loopback
by default, stores documents in the `inkpatch-data` volume, and passes the
environment settings below into the container. See [INSTALL.md](INSTALL.md) for
language packs, extra font packages, and manual `docker build`/`docker run`
commands.

For a remote host, public demo, reverse proxy, backup, or private tunnel, read
[HOSTING.md](HOSTING.md) first. A public deployment needs TLS, an outer
authentication layer, `TEXTEDIT_COOKIE_SECURE=1`, and a persistent data volume;
the per-document cookie is not a user login system.

## Configuration

Environment variables are read when the app starts.

| Variable | Default | Purpose |
| --- | --- | --- |
| `TEXTEDIT_DATA_DIR` | `./data` | Directory for document folders and previews |
| `TEXTEDIT_MAX_SIDE` | `2400` | Longest side of the normalized source image, in pixels |
| `TEXTEDIT_MAX_UPLOAD_MB` | `20` | Maximum upload size |
| `TEXTEDIT_MAX_LINES` | `600` | Maximum OCR lines retained per document |
| `TEXTEDIT_TTL_HOURS` | `96` | Hours without a document change before its whole folder (`image.png`, `preview.png`, `doc.json`) is deleted; cleanup runs at startup and hourly |
| `TEXTEDIT_COOKIE_SECURE` | `0` | Set to `1` when serving over HTTPS so document cookies include the `Secure` flag |
| `TEXTEDIT_OCR_TARGET_SIDE` | `2200` | Working resolution for OCR; small images are upscaled and large ones downscaled |
| `TEXTEDIT_OCR_MIN_CONFIDENCE` | `30` | Confidence floor for long-token rejection and line anchors; low-confidence real words are still kept |
| `TEXTEDIT_OCR_LINE_RECOVERY` | `1` | Set to `0` to disable OpenCV line detection and per-line Tesseract re-reads |
| `TEXTEDIT_OCR_RECOVERY_MAX_LINES` | `60` | Maximum suspect line crops re-OCR'd per scan |
| `TEXTEDIT_OCR_RECOVERY_BUDGET` | `60` | Time budget in seconds for the line-recovery pass (`0` disables the time limit) |
| `TEXTEDIT_PHOTO_CORRECT` | `1` | Set to `0` to disable perspective correction and deskewing during upload, rescan, and OCR |
| `TEXTEDIT_FONT_DIRS` | unset | Extra font directories (separated by the platform path separator) to search for matching faces |
| `TESSERACT_CMD` | auto-detected | Path to a `tesseract` binary, or to an extracted AppImage directory containing `AppRun` |
| `TESSDATA_PREFIX` | auto-detected | Tesseract language-data directory |
| `OMP_THREAD_LIMIT` | `2` | Thread cap used for OCR subprocesses |

Compose additionally reads `INKPATCH_PORT` (default `8000`) and `INKPATCH_BIND`
(default `127.0.0.1`; set `0.0.0.0` only if you intentionally want LAN access).
The image build accepts `TESSERACT_LANGS` (default `eng`) and `EXTRA_FONTS`.

## API

The browser uses the same API that is documented by FastAPI at
<http://127.0.0.1:8000/docs>.

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | App, OCR, limit, and demo status |
| `GET` | `/api/languages` | Available Tesseract languages |
| `GET` | `/api/fonts` | Font families available for explicit overrides |
| `POST` | `/api/documents` | Upload `file`, `lang`, and `psm` as multipart form data |
| `GET` | `/api/documents/{id}` | Document metadata and detected lines |
| `GET` | `/api/documents/{id}/image` | Straightened, size-limited working image |
| `POST` | `/api/documents/{id}/ocr` | Re-run OCR with `{ "lang": "eng", "psm": 3 }` |
| `POST` | `/api/documents/{id}/render` | Render edits and return a preview URL |
| `GET` | `/api/documents/{id}/preview` | Latest rendered preview |
| `GET` | `/api/documents/{id}/download` | Download the current PNG |
| `DELETE` | `/api/documents/{id}` | Delete the document and its files |
| `POST` | `/api/demo` | Create a document from the bundled demo |
| `GET` | `/api/demo/image` | Demo source image |

`POST /api/documents` and `POST /api/demo` each set a random `HttpOnly` cookie
named `inkpatch_doc_<id-without-hyphens>`. The cookie is scoped to
`Path=/api/documents/<id>` with `SameSite=Strict`, and `doc.json` stores only a
SHA-256 hash of its value. Every route under `/api/documents/<id>` requires that
document's cookie: a missing or wrong cookie returns `403`, while an unknown or
expired document returns `404`. Successful requests refresh the cookie lifetime;
deleting a document clears it. Upload another image to receive another,
independent cookie — one document's cookie cannot authorize another document.
There is deliberately no endpoint that lists documents. Documents created before
per-document cookies (or by internal corpus scripts without a token) have no
token hash and are denied by the API.

Command-line clients must retain cookies between requests, for example with
`curl -c cookies.txt -b cookies.txt`.

A render request contains only changed lines:

```json
{
  "edits": [
    {
      "line_id": "L001",
      "text": "New headline",
      "font": "auto",
      "font_family": null,
      "bold": null,
      "italic": null,
      "underline": null,
      "strikethrough": null,
      "shadow": null,
      "background": null,
      "color": null,
      "align": "auto",
      "scale": 1.0,
      "font_size": null
    }
  ]
}
```

A new text box uses the same edit shape plus a `box` geometry and a
client-only synthetic `line_id`:

```json
{
  "edits": [
    {
      "line_id": "N001",
      "text": "Freshly roasted",
      "box": { "x": 120, "y": 240, "width": 320, "height": 48 },
      "font_size": 30,
      "font": "serif",
      "background": null
    }
  ]
}
```

`font` accepts `auto`, `sans`, `serif`, or `mono`. `font_family` optionally
names a concrete family from `/api/fonts` (for example `"Liberation Sans"`)
and takes precedence over the broad category. `color` is a six-digit hex string
such as `#1f3a2d`, or `null` to inherit. `scale` must be between `0.4` and
`3.0`; `align` accepts `auto`, `left`, `center`, or `right`. `font_size` is an
optional absolute base size in working-image pixels, used by new boxes and
explicit style copies. `underline`, `strikethrough`, and `shadow` accept `null`
(match the source), `true` (force the effect on), or `false` (remove it).
`background` accepts `null` (keep the repaired original) or a six-digit hex
colour; the fill is clipped to the canvas and split around every other
selection box.

## Project layout

```text
app/
  main.py         FastAPI routes, upload limits, static app, cookie middleware
  auth.py         Per-document cookie names, token hashing, and scopes
  config.py       Paths, environment configuration, limits
  fonts.py        Bundled/system font registry, family matching, catalogue
  ocr.py          Tesseract discovery, photo preprocessing, TSV parsing
  imaging.py      Image normalization, style sampling, inpainting, rendering
  storage.py      UUID document store, OCR orchestration, TTL cleanup
  schemas.py      Pydantic request validation
  static/         Vanilla HTML/CSS/JS frontend and favicon
  assets/
    fonts/        Bundled DejaVu/Liberation/Noto faces and licenses
    demo/         Bundled demo poster
scripts/
  make_demo.py    Regenerates the demo poster
requirements.txt  Python runtime dependencies
Dockerfile        Python slim image with Tesseract and a non-root user
compose.yaml      Local Compose service with a persistent volume
INSTALL.md        Platform-specific setup commands
HOSTING.md        Hosting, privacy, authentication, backup, and sizing plan
```

At runtime, documents are stored as:

```text
data/<uuid>/
  image.png       Straightened, size-limited working image
  preview.png     Latest render, when one exists
  doc.json        OCR lines, sampled styles, metadata, access-token hash
```

## Privacy and operational notes

- **Default local deployment:** uploads, OCR, rendering, and storage are
  entirely local. There are no remote API calls, telemetry, or model downloads,
  and no image leaves the host. This is the privacy promise for `uvicorn` or
  Compose on loopback.
- **Remote or public hosting is different.** If you put InkPatch on a cloud VM,
  a home server reached through a tunnel, or any address other than loopback,
  uploads leave the user's device and are stored on that host. You then need
  TLS, an outer authentication layer, a privacy notice, and a backup policy.
  The per-document cookie protects one document; it is not a login system.
  See [HOSTING.md](HOSTING.md) for the full deployment and privacy plan.
- Each document is protected by a random per-document browser cookie. Cookies
  are `HttpOnly`, `SameSite=Strict`, scoped to the document's API path, and
  optionally `Secure` (`TEXTEDIT_COOKIE_SECURE=1`). Only a SHA-256 hash of each
  token is stored. This is document-capability access, not a user-account or
  multi-user authorization system, and there is no gallery or listing endpoint.
- Keep the app bound to `127.0.0.1` unless you put it behind an authenticated
  TLS reverse proxy or a private tunnel. A document cookie authorizes only the
  document it names. The upload and demo creation endpoints do not require a
  cookie, so a public deployment must protect the whole site at the proxy.
- OCR is serialized inside the process, and rendering is locked per document.
  Run a single Uvicorn worker; this is a single-user, single-instance app.
- Edits live in the browser session. Download the PNG before closing the tab or
  starting a new image. The server does not keep an undo history.
- A document folder that has not changed for `TEXTEDIT_TTL_HOURS` (default 96,
  four days) is deleted at startup and checked again hourly. This removes
  `image.png`, `preview.png`, and `doc.json` together. Viewing or downloading a
  document does not reset its expiry.
- Documents stored before per-document cookies were introduced have no token
  hash and are denied by the API; upload them again if they are still needed.
- Deleting the `data/` directory or the Compose volume removes all stored
  documents. There is no encryption at rest; use full-disk encryption or an
  encrypted volume if that matters.
- For remote backups, remember that `doc.json` contains only the token hash.
  A restored document still needs the original browser cookie; there is no
  admin listing or token-recovery endpoint.

## Limits

- OCR quality depends on resolution, contrast, language packs, and layout. A
  different page-segmentation mode often fixes missed or merged lines. InkPatch
  also re-reads suspect lines one at a time after the page passes; disable
  `TEXTEDIT_OCR_LINE_RECOVERY` if that extra work is not wanted.
- Line recovery is bounded by `TEXTEDIT_OCR_RECOVERY_MAX_LINES` and
  `TEXTEDIT_OCR_RECOVERY_BUDGET`. Very large pages may still contain lines that
  neither the page pass nor the recovery budget reaches; re-scanning with
  **Sparse text** can help.
- Shadow detection is intentionally conservative. A very soft, low-contrast
  shadow may not be reproduced, and no shadow is invented when the evidence is
  ambiguous. The original shadow is removed with the old glyphs either way.
  **Shadow → On** is an explicit user request and adds a subtle synthetic drop
  shadow when no original shadow was detected.
- Background fills are flat rectangles. A fill follows replacement text that
  grows into free space, while staying clipped to the canvas and split around
  every other detected or newly created selection box, so another line is never
  covered. A replacement whose glyphs extend past the last free area can still
  outrun the filled rectangle.
- **Handwriting is the hardest case.** Neat print handwriting in a sharp,
  evenly lit photo can work, especially with the automatic layout mode and the
  sparse-text option. Flowing cursive, mixed scripts, and very faint pencil are
  beyond Tesseract's LSTM models; expect to correct some lines manually.
- Background repair is best on flat colours, cards, and simple gradients. Text
  over busy photography uses inpainting and may still leave a small smudge;
  the repair mask is deliberately generous to avoid old-text halos.
- Style detection is heuristic. It recognises common Latin document faces
  well, but decorative type, unusual fonts, curved text, and very small text
  should be checked visually and can be overridden.
- Browser/OS icons and pictographic emoji are kept as untouched source pixels,
  not editable text. Tesseract can guess a different character for an icon, and
  the renderer draws each selection with one typeface, so faithful icon/emoji
  editing is not reliable. Textual marks such as `©`, `°`, arrows, currency
  signs and math operators remain editable.
- Replacement text keeps its detected point size and grows into the free
  horizontal space around the line, stopping before side-by-side content. A
  replacement that cannot fit anywhere on the canvas is reduced instead of
  reflowing the whole document; use the text-size control to override it.
- Images are downscaled so the longest side is at most `TEXTEDIT_MAX_SIDE`
  before storage; OCR then works at `TEXTEDIT_OCR_TARGET_SIDE`.
- Non-Latin editing needs both a Tesseract language pack and a system font that
  can draw the script. Chinese, Hindi, and Kannada are covered by the verified
  package commands in [INSTALL.md](INSTALL.md); for Docker, build with extra
  fonts such as `fonts-noto-cjk` (CJK) and `fonts-noto-core` (Devanagari and
  Kannada).
- Arabic and Hebrew word order follows Tesseract's TSV reading order. A
  mixed-script line is ordered by its majority script, so an embedded run can
  still be visually imperfect; split such a line or use sparse-text mode.
- Replacement glyphs come from the detected or overridden face. A rare symbol
  missing from that face can render as a missing-glyph box; the bundled DejaVu
  faces cover most common currency, legal, punctuation, arrow and math marks.

## Third-party assets

The bundled DejaVu fonts are distributed under the Bitstream Vera license; see
`app/assets/fonts/LICENSE.txt`. Liberation fonts are under the SIL Open Font
License; see `app/assets/fonts/LICENSE-Liberation.txt`. Noto fonts are under the
SIL Open Font License; see `app/assets/fonts/LICENSE-Noto.txt`. Tesseract,
OpenCV, Pillow, NumPy, FastAPI, and Uvicorn retain their respective licenses.
The demo poster is generated by `scripts/make_demo.py`.
