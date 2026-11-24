# Install and run

## Table of contents

- [Guided installer](#guided-installer)
- [Fedora / RHEL family](#fedora--rhel-family)
- [Ubuntu / Lubuntu](#ubuntu--lubuntu)
- [Arch Linux / Manjaro](#arch-linux--manjaro)
- [openSUSE / SLES](#opensuse--sles)
- [Alpine Linux](#alpine-linux)
- [FreeBSD and DragonFly BSD](#freebsd-and-dragonfly-bsd)
- [OpenBSD](#openbsd)
- [NetBSD](#netbsd)
- [macOS](#macos)
- [Installing Docker or Podman](#installing-docker-or-podman)
- [Docker](#docker)
- [Hosted deployment](#hosted-deployment)
- [Phone, tablet, and desktop layout](#phone-tablet-and-desktop-layout)
- [Photo and handwriting tips](#photo-and-handwriting-tips)
- [OCR line recovery](#ocr-line-recovery)
- [Symbols and decoration](#symbols-and-decoration)
- [Windows (native)](#windows-native)
- [Podman](#podman)
- [Android / Termux (best effort)](#android--termux-best-effort)

InkPatch bundles its Latin fonts (DejaVu, Liberation, and Noto), so no extra
font package is required for English/European text. System fonts are discovered
automatically as additional matching candidates. For CJK (including Chinese),
Devanagari (including Hindi), Kannada, Arabic, or Hebrew text, install a
matching Noto font package as shown below. Tesseract language data is needed for
OCR; the font is needed to draw replacement text.

The language codes used below are `deu` (German), `chi_sim` (Chinese,
Simplified; use `chi_tra` for Traditional), `hin` (Hindi), and `kan` (Kannada).

## Guided installer

On Linux, BSD, or macOS:

```bash
./install.sh
```

On Windows, run `install.bat` from Command Prompt or PowerShell.

The installer detects Debian/Ubuntu and derivatives (`apt`), Fedora and the
RHEL/CentOS/Rocky/AlmaLinux family (`dnf`/`yum`), Arch Linux and Manjaro
(`pacman`), openSUSE/SLES (`zypper`), Alpine Linux (`apk`), FreeBSD and
DragonFly BSD (`pkg`), OpenBSD (`pkg_add`), NetBSD (`pkgin`/`pkg_add`), and
macOS (Homebrew). It prints a short ASCII logo and uses terminal colour on an
interactive terminal; set `NO_COLOR=1` or use `TERM=dumb` to disable colour.
The script uses POSIX `sh`, so the base shells on BSD and Linux can run it
directly.

The guided installer checks the operating system, Python 3.10+, the Python
`venv` module, Tesseract with English (`eng`) data, and the OpenCV runtime. It
creates `.venv` and installs `requirements.txt` with:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On a BSD base shell or Alpine's BusyBox shell, activate the environment with
`. .venv/bin/activate`; `source` is only available in bash/zsh-style shells.

It does **not** run OS package managers itself. If Python, Tesseract, or a
system library is missing, it prints the matching `apt`, `dnf`, `pacman`,
`zypper`, `apk`, `pkg`/`pkg_add`, `brew`, or Windows `winget` command. It only
asks about Docker versus the native install when Docker or Podman is already
present. With neither runtime installed it prints per-platform Docker/Podman
installation commands and continues with the native install. A container choice
prints the Compose or direct build/run commands, including the optional
`TESSERACT_LANGS` and `EXTRA_FONTS` build arguments, without building anything.
Android/Termux is rejected by the automated installer but the script prints the
Termux packages and manual venv commands first. Other unsupported systems exit
with a pointer to the manual sections below. At the end it prints the command to
start InkPatch.

It also checks optional Tesseract language data (`deu`, `chi_sim`, `chi_tra`,
`hin`, and `kan`) and the Noto CJK, Devanagari, and Kannada fonts. Missing
optional packages do not block startup; the installer prints the matching
per-platform package-manager or font instructions so they can be added when
needed.

## Fedora / RHEL family

On Fedora and compatible systems:

```bash
sudo dnf install -y python3 python3-pip tesseract tesseract-langpack-eng mesa-libGL glib2 libgomp
cd textedit
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

On RHEL, CentOS Stream, Rocky Linux, or AlmaLinux, enable EPEL first because
Tesseract is packaged there:

```bash
sudo dnf install -y epel-release
```

Additional OCR languages:

```bash
sudo dnf install -y tesseract-langpack-deu tesseract-langpack-chi_sim \
    tesseract-langpack-chi_tra tesseract-langpack-hin tesseract-langpack-kan
```

For editing Chinese, Hindi, or Kannada replacement text, install the matching
Noto fonts too:

```bash
sudo dnf install -y google-noto-sans-cjk-fonts \
    google-noto-sans-devanagari-fonts google-noto-sans-kannada-fonts
```

## Ubuntu / Lubuntu

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip tesseract-ocr tesseract-ocr-eng libgl1 libglib2.0-0 libgomp1
cd textedit
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Additional OCR languages:

```bash
sudo apt install -y tesseract-ocr-deu tesseract-ocr-chi-sim \
    tesseract-ocr-chi-tra tesseract-ocr-hin tesseract-ocr-kan
```

For editing Chinese, Hindi, or Kannada replacement text, install the matching
Noto fonts too:

```bash
sudo apt install -y fonts-noto-cjk fonts-noto-core
```

## Arch Linux / Manjaro

```bash
sudo pacman -S --needed python python-pip tesseract tesseract-data-eng glib2 gcc-libs
cd textedit
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Additional OCR languages:

```bash
sudo pacman -S --needed tesseract-data-deu tesseract-data-chi_sim \
    tesseract-data-chi_tra tesseract-data-hin tesseract-data-kan
```

For editing Chinese, Hindi, or Kannada replacement text, install the matching
Noto fonts too:

```bash
sudo pacman -S --needed noto-fonts noto-fonts-cjk
```

## openSUSE / SLES

```bash
sudo zypper install -y python3 python3-pip python3-virtualenv \
    tesseract-ocr tesseract-ocr-traineddata-eng libglib-2_0-0 libgomp1
cd textedit
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Tumbleweed provides these packages directly. On Leap or SLES, add a repository
that provides `tesseract-ocr` if it is not in your enabled repositories.

Additional OCR languages:

```bash
sudo zypper install -y tesseract-ocr-traineddata-deu \
    tesseract-ocr-traineddata-chi_sim tesseract-ocr-traineddata-chi_tra \
    tesseract-ocr-traineddata-hin tesseract-ocr-traineddata-kan
```

For editing Chinese, Hindi, or Kannada replacement text, install the matching
Noto fonts too:

```bash
sudo zypper install -y google-noto-sans-cjk-fonts \
    noto-sans-devanagari-fonts noto-sans-kannada-fonts
```

## Alpine Linux

```bash
sudo apk add python3 py3-pip py3-virtualenv tesseract-ocr \
    tesseract-ocr-data-eng glib libgomp
cd textedit
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Alpine uses musl libc. Some NumPy/OpenCV wheels are unavailable, so pip may build
them from source. If that happens, install the build packages and rerun:

```bash
sudo apk add build-base cmake ninja openblas-dev libjpeg-turbo-dev \
    libpng-dev libwebp-dev tiff-dev
```

The Docker/Podman image is the easier route when a container runtime is
available.

Additional OCR languages:

```bash
sudo apk add tesseract-ocr-data-deu tesseract-ocr-data-chi_sim \
    tesseract-ocr-data-chi_tra tesseract-ocr-data-hin tesseract-ocr-data-kan
```

For editing Chinese, Hindi, or Kannada replacement text, install the matching
Noto fonts too:

```bash
sudo apk add font-noto-cjk font-noto-devanagari font-noto-kannada
```

## FreeBSD and DragonFly BSD

On FreeBSD:

```bash
sudo pkg install -y python3 py311-pip tesseract
cd textedit
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Adjust `py311-pip` if `python3` uses another version; `pkg search
'^py[0-9]+-pip$'` lists the available packages. FreeBSD's `tesseract` package
depends on `tesseract-data`, which includes the language models. PyPI wheels for
NumPy, Pillow, and OpenCV may be unavailable, so pip can build them from source.

Optional Noto fonts:

```bash
sudo pkg install -y noto-extra noto-sc noto-tc
```

`noto-extra` covers Devanagari and Kannada; `noto-sc` and `noto-tc` cover
Simplified and Traditional Chinese.

Podman is available on FreeBSD:

```bash
sudo pkg install -y podman
sudo service podman enable
```

The `podman-suite` package adds extra tooling. Docker is not native on FreeBSD.
DragonFly BSD uses the same `pkg` interface through DPorts, but package
availability and versions can lag FreeBSD; search with `pkg search` before
installing optional language or font packages.

## OpenBSD

```bash
doas pkg_add python3 py3-pip tesseract
cd textedit
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Tesseract's English data is installed with the engine package. Additional
language packs:

```bash
doas pkg_add tesseract-deu tesseract-chi_sim tesseract-chi_tra \
    tesseract-hin tesseract-kan
```

For CJK replacement text:

```bash
doas pkg_add noto-cjk
```

Devanagari and Kannada font package names vary by release. Search with
`pkg_info -Q noto` and install the matching faces if available; otherwise put
the `.ttf` files in a folder listed by `TEXTEDIT_FONT_DIRS`.

OpenBSD does not provide Docker or Podman. Use the native environment above, or
run the container on another Linux host.

## NetBSD

```bash
sudo pkgin install python311 py311-pip tesseract
cd textedit
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Tesseract language-data packaging varies by pkgsrc release. Search with
`pkgin search tesseract`, and copy missing `*.traineddata` files into the
tessdata directory used by your pkgsrc install (commonly
`/usr/pkg/share/tessdata`) or the directory named by `TESSDATA_PREFIX`.

Optional Noto fonts:

```bash
sudo pkgin install noto-fonts noto-cjk-fonts
```

Podman is available through pkgsrc but runs Linux containers in a virtual
machine; the native Python install is recommended on NetBSD.

## macOS

Install Homebrew if it is not already available:

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Install and run:

```bash
brew install python tesseract
cd textedit
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Additional OCR languages:

```bash
brew install tesseract-lang
```

The `tesseract-lang` formula installs the full `tessdata_fast` set, so
`chi_sim`, `hin`, and `kan` are included; there are no separate per-language
formulae.

For editing Chinese, Hindi, or Kannada replacement text, install the matching
Noto fonts too:

```bash
brew install --cask font-noto-sans-cjk font-noto-sans-devanagari \
    font-noto-sans-kannada
```

## Installing Docker or Podman

The guided installer prints the matching commands when neither runtime is
detected, then continues with the native install unless you install a runtime
and rerun it. The installer never runs these commands itself.

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y docker.io docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"    # log out and back in afterwards
```

Podman instead:

```bash
sudo apt update
sudo apt install -y podman podman-compose
```

Fedora/RHEL family, with Podman recommended:

```bash
sudo dnf install -y podman podman-compose
```

On RHEL, CentOS Stream, Rocky Linux, or AlmaLinux, install `epel-release` if
`podman-compose` is not available. For Docker on Fedora, follow
<https://docs.docker.com/engine/install/fedora/>; on the RHEL family, use your
distribution or vendor repository.

Arch Linux/Manjaro:

```bash
sudo pacman -S --needed docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Use `podman podman-compose` with `pacman -S --needed` instead if you prefer
Podman.

openSUSE/SLES:

```bash
sudo zypper install -y docker docker-compose
sudo systemctl enable --now docker
sudo usermod -aG docker "$USER"
```

Use `podman podman-compose` with `zypper install -y` for Podman.

Alpine Linux:

```bash
sudo apk add docker docker-cli-compose
sudo rc-update add docker default
sudo service docker start
```

Use `sudo apk add podman podman-compose` for Podman.

FreeBSD:

```bash
sudo pkg install -y podman
sudo service podman enable
```

Docker is not native on FreeBSD. OpenBSD, NetBSD, and DragonFly BSD have no
native Docker support; use the native install or a Linux host.

macOS:

```bash
brew install --cask docker
open -a Docker
```

Or Podman Desktop's CLI:

```bash
brew install podman
podman machine init
podman machine start
```

## Docker

Build and start:

```bash
docker compose up --build -d
```

Open <http://127.0.0.1:8000>.

Logs:

```bash
docker compose logs -f
```

Stop:

```bash
docker compose down
```

Change the host port:

```bash
INKPATCH_PORT=9000 docker compose up -d
```

### Docker language packs

Build with one or more Tesseract language codes:

```bash
TESSERACT_LANGS="eng deu fra" docker compose build
docker compose up -d
```

The Dockerfile converts underscores to hyphens for Debian package names, so
`chi_sim` installs `tesseract-ocr-chi-sim`; `hin` and `kan` install
`tesseract-ocr-hin` and `tesseract-ocr-kan`.

Non-Latin scripts also need a font package:

```bash
TESSERACT_LANGS="eng ara" EXTRA_FONTS="fonts-noto-core" docker compose build
docker compose up -d
```

CJK text:

```bash
TESSERACT_LANGS="eng chi_sim" EXTRA_FONTS="fonts-noto-cjk" docker compose build
docker compose up -d
```

Chinese (Simplified), Hindi, and Kannada together:

```bash
TESSERACT_LANGS="eng chi_sim hin kan" \
EXTRA_FONTS="fonts-noto-cjk fonts-noto-core" \
docker compose build
docker compose up -d
```

`fonts-noto-cjk` supplies the Chinese glyphs; `fonts-noto-core` supplies the
Devanagari and Kannada faces. Use `chi_tra` instead of `chi_sim` for Traditional
Chinese.

### Docker without Compose

```bash
docker build -t inkpatch .
docker run --rm -p 127.0.0.1:8000:8000 -v inkpatch-data:/data inkpatch
```

To include extra languages, pass the same build arguments:

```bash
docker build \
    --build-arg TESSERACT_LANGS="eng chi_sim hin kan" \
    --build-arg EXTRA_FONTS="fonts-noto-cjk fonts-noto-core" \
    -t inkpatch .
docker run --rm -p 127.0.0.1:8000:8000 -v inkpatch-data:/data inkpatch
```

### Docker data

Documents persist in the `inkpatch-data` volume. Delete the volume and its
documents with:

```bash
docker compose down -v
```

Run at <http://127.0.0.1:8000>.

## Hosted deployment

The commands above are for a local or LAN deployment. If InkPatch will be
reachable from the internet, read [HOSTING.md](HOSTING.md) before exposing it.
At minimum:

- Keep InkPatch bound to `127.0.0.1` and put Caddy, Nginx, Cloudflare Tunnel, or
  Tailscale in front. Do not publish container port 8000 directly.
- Protect the whole site with an outer login: Cloudflare Access, Tailscale
  identity, an OAuth proxy, or reverse-proxy basic auth. The per-document cookie
  is a capability for one document, not a user account system, and the upload
  and demo endpoints are unauthenticated without an outer layer.
- Set `TEXTEDIT_COOKIE_SECURE=1` when the browser uses HTTPS.
- Keep `/data` on a persistent volume or bind mount. Free PaaS filesystems are
  often ephemeral and will delete documents on restart.
- Use a single Uvicorn worker and size the host for OCR: 2 GB RAM is the
  practical minimum, 4 GB is recommended.
- Shorten `TEXTEDIT_TTL_HOURS` for a public demo, and take encrypted backups if
  the documents matter.

[HOSTING.md](HOSTING.md) includes free-tier comparisons, an Oracle Cloud
walkthrough, home/Tailscale and Cloudflare Tunnel setups, reverse-proxy
examples, backup/restore commands, resource profiles, monitoring, and optional
OCR/AI sidecar guidance.

## Phone, tablet, and desktop layout

The browser UI is responsive across screen sizes. The landing view becomes one
column at `≤1020px`; at `≤1100px` the editor switches to a full-width canvas
with the text-line list and inspector as slide-in drawers. Touch controls are
enlarged (most buttons are 38px), `safe-area-inset` padding protects notches
and home indicators, and short landscape viewports scroll the editor instead
of compressing it.

To open InkPatch from a phone or tablet on the same network, bind the server to
the LAN instead of loopback. For a direct Python server:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

With Compose, set `INKPATCH_BIND=0.0.0.0`. Then open
`http://<computer-ip>:8000` on the other device. InkPatch uses per-document
browser cookies but has no user accounts, so only expose it on a trusted
network. Leave `TEXTEDIT_COOKIE_SECURE=0` for plain HTTP; set it to `1` when
TLS terminates in front of InkPatch.

## Photo and handwriting tips

The OCR pipeline flattens uneven lighting, straightens a photographed page, and
tries normal, inverted, and thresholded images. For the best results:

- Fill the frame with the page and keep the camera roughly parallel to it.
- Use even, diffuse light; avoid a hard shadow across the writing. InkPatch
  can preserve a clear drop shadow that belongs to a text line, but it will not
  invent one for clean text.
- Hold still and wait for focus before shooting.
- Use **Automatic** layout for a whole page, or **Sparse text** for a few
  scattered notes.
- Neat print handwriting works far better than cursive. Tesseract's models are
  trained mainly on printed text, so very cursive or faint pencil notes may
  need manual corrections.

If perspective correction is too eager on a scanned page with a decorative
border, start the server with `TEXTEDIT_PHOTO_CORRECT=0`. The other photo
preprocessing steps still run.

## OCR line recovery

InkPatch normally re-reads lines that page segmentation missed or truncated.
The recovery pass detects ink rows in both polarities (so a light-on-dark
banner inside a light page is not invisible), crops each suspect line, and
re-reads it with Tesseract single-line mode before merging the result with the
page passes. It is enabled by default and can be tuned without changing code:

```bash
TEXTEDIT_OCR_LINE_RECOVERY=1 \
TEXTEDIT_OCR_RECOVERY_MAX_LINES=60 \
TEXTEDIT_OCR_RECOVERY_BUDGET=60 \
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- `TEXTEDIT_OCR_LINE_RECOVERY=0` disables the extra per-line passes.
- `TEXTEDIT_OCR_RECOVERY_MAX_LINES` caps how many suspect crops are re-read.
- `TEXTEDIT_OCR_RECOVERY_BUDGET` caps the total time in seconds; `0` removes the
  time cap (the line cap still applies).

## Symbols and decoration

Inside a recognised line, InkPatch keeps every textual Unicode character:
letters, combining marks, digits, punctuation, currency/math/legal marks,
arrows, and similar symbols. This means lines such as `© 2026`, `24 °C`,
`R&D`, `₹ 500`, `± 5%`, `x ≤ 10`, `Next →`, and `§ 4` remain complete and
editable.

A line containing only symbols is accepted when the symbols are textual. It is
not accepted for icon and ornament ranges (box/block drawing, geometric
shapes, dingbats, pictographs, emoji, Braille, or musical symbols), repeated
rules such as `-----`, `#####`, `*****`, and `•••••`, or a lone list marker
(`•`, `◆`, `∙`, and similar). Ellipsis tokens such as `...` are retained after
real text and masked with the line, but a dot-only row is not editable. Emoji,
ornaments, and bullets that occur *inside* a real text line are retained with
that line and removed along with it when the line is edited.

Arabic and Hebrew lines preserve Tesseract's TSV reading order rather than
being force-sorted left-to-right. Word gaps are measured visually, so RTL
words still receive normal spaces.

## Windows (native)

All Python dependencies install as prebuilt wheels on Windows, and no compiler
is needed. InkPatch already probes the usual Windows Tesseract layout
(`C:\Program Files\Tesseract-OCR`) and the system font directory
(`C:\Windows\Fonts`) automatically.

1. Install Python 3.10 or newer from <https://python.org> and tick
   *"Add python.exe to PATH"*. You can also use WinGet:

   ```bat
   winget install -e --id Python.Python.3.13
   ```

2. Install the Tesseract CLI (the common choice is the UB Mannheim build,
   <https://github.com/UB-Mannheim/tesseract/wiki>). Its English data is
   included. You can also use WinGet:

   ```bat
   winget install -e --id UB-Mannheim.TesseractOCR
   ```

   Re-run the Tesseract installer to add German, Chinese (Simplified or
   Traditional), Hindi, and Kannada under the additional language/script models,
   or copy `deu.traineddata`, `chi_sim.traineddata`, `chi_tra.traineddata`,
   `hin.traineddata`, and `kan.traineddata` from the
   official tessdata repository into `C:\Program Files\Tesseract-OCR\tessdata`.
   Verify with `tesseract --list-langs`. The default install directory is found
   automatically, or make sure `tesseract.exe` is on PATH, or set
   `TESSERACT_CMD`/`TESSDATA_PREFIX` explicitly.
3. Build and run from a terminal (Command Prompt or PowerShell) in the
   project directory:

   ```bat
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

4. Open <http://127.0.0.1:8000>.

Windows normally includes Microsoft YaHei (Simplified Chinese), Nirmala UI and
Mangal (Devanagari), and Tunga (Kannada) once the matching language features are
installed. If a script shows missing-glyph boxes, add the language under
Settings → Time & Language → Language & region, or put a Noto font in a folder
listed by `TEXTEDIT_FONT_DIRS` and restart InkPatch.

Environment variables are set per shell: `set VAR=value` in Command Prompt or
`$env:VAR = "value"` in PowerShell. Extra font directories are listed with the
platform path separator (`;` on Windows) in `TEXTEDIT_FONT_DIRS`.

## Podman

The bundled `Dockerfile` and `compose.yaml` are standard container builds and
also work with Podman, including rootless Podman: the image runs as a fixed
non-root user, drops all capabilities, and documents persist in the named
`inkpatch-data` volume (no UID-mapping issues). Port 8000 is above 1024, so no
privileged binding is required.

With a compose provider installed (`podman-compose`, or Docker Compose used as
the provider behind Podman's `podman compose` wrapper):

```bash
podman compose up --build -d
```

Language packs and extra fonts use the same build arguments as Docker
(`TESSERACT_LANGS`, `EXTRA_FONTS`). Without compose:

```bash
podman build -t inkpatch .
podman run --rm -p 127.0.0.1:8000:8000 -v inkpatch-data:/data inkpatch
```

Chinese (Simplified), Hindi, and Kannada, for example:

```bash
podman build \
    --build-arg TESSERACT_LANGS="eng chi_sim hin kan" \
    --build-arg EXTRA_FONTS="fonts-noto-cjk fonts-noto-core" \
    -t inkpatch .
podman run --rm -p 127.0.0.1:8000:8000 -v inkpatch-data:/data inkpatch
```

Delete the documents volume with `podman volume rm inkpatch-data`.

## Android / Termux (best effort)

InkPatch is a local web server, so it can run on a phone inside Termux and be
used from the phone's (or another device's) browser. Android's bionic libc
cannot load PyPI wheels, so compiled libraries must come from the Termux
repositories; package names below are verified prefixes in
`termux/termux-packages`. Expect slower OCR on large photos than on a desktop.

```bash
pkg update
pkg install python python-numpy python-pillow tesseract curl
pkg install x11-repo
pkg install python-opencv-python    # OpenCV binding from the X11 repository
pkg install rust maturin            # needed to build pydantic-core
python -m venv --system-site-packages .venv
source .venv/bin/activate
pip install "fastapi>=0.115,<1.0" "uvicorn>=0.30,<1.0" \
            "pydantic>=2.8,<3.0" "python-multipart>=0.0.9,<1.0"
export TESSDATA_PREFIX=$PREFIX/share/tessdata
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- Do **not** `pip install numpy`, `Pillow`, or OpenCV: install the Termux
  packages instead and make the venv see them with `--system-site-packages`.
  The `pip install` line only adds the pure-Python packages.
- The Termux `tesseract` package bundles English trained data in
  `$PREFIX/share/tessdata`; the `TESSDATA_PREFIX` export above makes InkPatch's
  auto-detection find it. Additional languages are `.traineddata` files copied
  to that directory. For Chinese (Simplified), Hindi, and Kannada:

  ```bash
  cd $PREFIX/share/tessdata
  curl -LO https://github.com/tesseract-ocr/tessdata_fast/raw/main/chi_sim.traineddata
  curl -LO https://github.com/tesseract-ocr/tessdata_fast/raw/main/hin.traineddata
  curl -LO https://github.com/tesseract-ocr/tessdata_fast/raw/main/kan.traineddata
  ```
- `pydantic` pulls `pydantic-core`, a Rust extension; with `rust` and
  `maturin` installed the first `pip install` compiles it on device, which
  takes a while (this is the slowest step).
- Containers (Docker/Podman) do not run inside Termux; use the native venv
  flow above. `proot-distro` environments are possible but not required.
- `termux-wake-lock` keeps the session from being suspended mid-OCR.
- The bundled fonts cover English and European text. Android's `/system/fonts`
  is searched automatically; if a CJK, Devanagari, or Kannada face is missing,
  put a Noto font file in `~/.local/share/fonts` (also searched) or point
  `TEXTEDIT_FONT_DIRS` at the folder containing it, then restart InkPatch.
- On slower phones, consider `TEXTEDIT_MAX_SIDE=1600` and
  `TEXTEDIT_OCR_TARGET_SIDE=1200` to cap the working resolution.

