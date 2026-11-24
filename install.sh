#!/bin/sh
# InkPatch guided installer for Linux, BSD, and macOS.
#
# This script only executes the Python virtual-environment commands:
#   python3 -m venv .venv
#   . .venv/bin/activate
#   python -m pip install -r requirements.txt
#
# Python, Tesseract, OpenCV system libraries, Docker, and Podman are detected.
# When one of those OS packages is missing, the script prints the package-manager
# command to install it instead of running sudo/apt/dnf/pacman/zypper/apk/pkg
# itself.  The script is POSIX sh so it also runs with the base system shells on
# FreeBSD, OpenBSD, NetBSD, DragonFly BSD, macOS, and Linux.

set -u

ROOT="$(CDPATH= cd "$(dirname "$0")" && pwd)"
cd "$ROOT" || {
    printf 'Error: cannot enter %s\n' "$ROOT" >&2
    exit 1
}

VENV_DIR="$ROOT/.venv"
VENV_PY="$VENV_DIR/bin/python"
REQUIREMENTS="$ROOT/requirements.txt"

COLOR_RESET=""
COLOR_BOLD=""
COLOR_DIM=""
COLOR_CYAN=""
COLOR_GREEN=""
COLOR_YELLOW=""
COLOR_RED=""

setup_colours() {
    COLOR_RESET=""
    COLOR_BOLD=""
    COLOR_DIM=""
    COLOR_CYAN=""
    COLOR_GREEN=""
    COLOR_YELLOW=""
    COLOR_RED=""

    # Only use ANSI colours on an interactive terminal, and honour NO_COLOR
    # (https://no-color.org/) and dumb terminals.
    if [ -t 1 ] && [ -z "${NO_COLOR+x}" ] && [ "${TERM:-dumb}" != "dumb" ]; then
        esc="$(printf '\033')"
        COLOR_RESET="${esc}[0m"
        COLOR_BOLD="${esc}[1m"
        COLOR_DIM="${esc}[2m"
        COLOR_CYAN="${esc}[36m"
        COLOR_GREEN="${esc}[32m"
        COLOR_YELLOW="${esc}[33m"
        COLOR_RED="${esc}[31m"
    fi
}

print_icon() {
    while IFS= read -r line; do
        printf '%s%s%s\n' "$COLOR_BOLD$COLOR_CYAN" "$line" "$COLOR_RESET"
    done <<'EOF'
   ___ ____
  |_ _|  _ \
   | || |_) |
   | ||  __/
  |___|_|
EOF
    printf '%sInkPatch installer%s\n' "$COLOR_BOLD" "$COLOR_RESET"
}

section() { printf '\n%s== %s ==%s\n' "$COLOR_BOLD$COLOR_CYAN" "$1" "$COLOR_RESET"; }
info() { printf '%s\n' "$*"; }
success() { printf '%s%s%s\n' "$COLOR_GREEN" "$*" "$COLOR_RESET"; }
error() { printf '%sError: %s%s\n' "$COLOR_RED" "$*" "$COLOR_RESET" >&2; }
warn() { printf '%s%s%s\n' "$COLOR_YELLOW" "$*" "$COLOR_RESET"; }
fail() { error "$*"; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

is_rhel_like() {
    case "${OS_ID:-}" in
        rhel|centos|centos-stream|rocky|almalinux|ol|oracle|amzn|scientific|cloudlinux)
            return 0
            ;;
    esac
    case " ${OS_LIKE:-} " in
        *rhel*|*centos*)
            return 0
            ;;
    esac
    return 1
}

unsupported_os() {
    printf '\n%sUnsupported operating system: %s%s\n' "$COLOR_RED" "$1" "$COLOR_RESET" >&2
    info "This installer supports:"
    info "  - Debian, Ubuntu, and derivatives with apt"
    info "  - Fedora and the RHEL/CentOS/Rocky/AlmaLinux family with dnf"
    info "  - Arch Linux and Manjaro with pacman"
    info "  - openSUSE and SLES with zypper"
    info "  - Alpine Linux with apk"
    info "  - FreeBSD and DragonFly BSD with pkg"
    info "  - OpenBSD with pkg_add"
    info "  - NetBSD with pkgin or pkg_add"
    info "  - macOS with Homebrew"
    info "  - Windows (run install.bat)"
    info "For other systems, follow INSTALL.md."
    exit 1
}

termux_install_instructions() {
    printf '\n%sUnsupported operating system: Android/Termux%s\n' "$COLOR_RED" "$COLOR_RESET" >&2
    info "The guided installer does not modify an Android/Termux environment."
    info "InkPatch can still run manually; install these Termux packages:"
    section "Termux packages"
    info "  pkg update"
    info "  pkg install python python-numpy python-pillow tesseract curl"
    info "  pkg install x11-repo"
    info "  pkg install python-opencv-python"
    info "  pkg install rust maturin"
    section "Python environment"
    info "  python -m venv --system-site-packages .venv"
    info "  source .venv/bin/activate"
    info '  pip install "fastapi>=0.115,<1.0" "uvicorn>=0.30,<1.0" \'
    info '              "pydantic>=2.8,<3.0" "python-multipart>=0.0.9,<1.0"'
    section "Run"
    info '  export TESSDATA_PREFIX=$PREFIX/share/tessdata'
    info "  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
    info "Then open http://127.0.0.1:8000 in the phone browser."
    info "See INSTALL.md -> Android / Termux for language data and font notes."
    info "Containers (Docker/Podman) do not run inside Termux; use this native flow."
    exit 1
}

detect_platform() {
    OS_FAMILY=""
    OS_LABEL=""
    OS_ID=""
    OS_LIKE=""
    PYTHON_INSTALL=""
    TESSERACT_INSTALL=""
    OPENCV_INSTALL=""
    BUILD_TOOLS_INSTALL=""
    ACTIVATE_CMD="source .venv/bin/activate"
    OS_KERNEL="$(uname -s 2>/dev/null || printf 'unknown')"

    case "$OS_KERNEL" in
        Linux)
            if [ -n "${TERMUX_VERSION:-}" ] || [ -d /data/data/com.termux ]; then
                termux_install_instructions
            fi

            if [ -r /etc/os-release ]; then
                # shellcheck disable=SC1091
                . /etc/os-release
                OS_ID="${ID:-}"
                OS_LIKE="${ID_LIKE:-}"
                OS_LABEL="${PRETTY_NAME:-${NAME:-Linux}}"
            else
                OS_LABEL="Linux"
            fi

            if [ "$OS_ID" = "alpine" ] || have apk; then
                OS_FAMILY="alpine"
                ACTIVATE_CMD=". .venv/bin/activate"
                PYTHON_INSTALL="sudo apk add python3 py3-pip py3-virtualenv"
                TESSERACT_INSTALL="sudo apk add tesseract-ocr tesseract-ocr-data-eng"
                OPENCV_INSTALL="sudo apk add glib libgomp"
                BUILD_TOOLS_INSTALL="sudo apk add build-base cmake ninja openblas-dev libjpeg-turbo-dev libpng-dev libwebp-dev tiff-dev"
            elif have pacman; then
                OS_FAMILY="arch"
                PYTHON_INSTALL="sudo pacman -S --needed python python-pip"
                TESSERACT_INSTALL="sudo pacman -S --needed tesseract tesseract-data-eng"
                OPENCV_INSTALL="sudo pacman -S --needed glib2 gcc-libs"
            elif have zypper; then
                OS_FAMILY="opensuse"
                PYTHON_INSTALL="sudo zypper install -y python3 python3-pip python3-virtualenv"
                TESSERACT_INSTALL="sudo zypper install -y tesseract-ocr tesseract-ocr-traineddata-eng"
                OPENCV_INSTALL="sudo zypper install -y libglib-2_0-0 libgomp1"
            elif have apt-get; then
                OS_FAMILY="debian"
                PYTHON_INSTALL="sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
                TESSERACT_INSTALL="sudo apt update && sudo apt install -y tesseract-ocr tesseract-ocr-eng"
                OPENCV_INSTALL="sudo apt update && sudo apt install -y libgl1 libglib2.0-0 libgomp1"
            elif have dnf || have yum; then
                OS_FAMILY="fedora"
                dnf_command="dnf"
                have dnf || dnf_command="yum"
                PYTHON_INSTALL="sudo $dnf_command install -y python3 python3-pip"
                if is_rhel_like; then
                    TESSERACT_INSTALL="sudo $dnf_command install -y epel-release && sudo $dnf_command install -y tesseract tesseract-langpack-eng"
                else
                    TESSERACT_INSTALL="sudo $dnf_command install -y tesseract tesseract-langpack-eng"
                fi
                OPENCV_INSTALL="sudo $dnf_command install -y mesa-libGL glib2 libgomp"
            else
                unsupported_os "$OS_LABEL"
            fi
            ;;
        FreeBSD)
            OS_FAMILY="freebsd"
            ACTIVATE_CMD=". .venv/bin/activate"
            OS_LABEL="FreeBSD $(uname -r 2>/dev/null || printf 'unknown')"
            PYTHON_INSTALL="sudo pkg install -y python3 py311-pip"
            TESSERACT_INSTALL="sudo pkg install -y tesseract"
            OPENCV_INSTALL=""
            ;;
        DragonFly)
            OS_FAMILY="dragonfly"
            ACTIVATE_CMD=". .venv/bin/activate"
            OS_LABEL="DragonFly BSD $(uname -r 2>/dev/null || printf 'unknown')"
            PYTHON_INSTALL="sudo pkg install -y python3 py311-pip"
            TESSERACT_INSTALL="sudo pkg install -y tesseract"
            OPENCV_INSTALL=""
            ;;
        OpenBSD)
            OS_FAMILY="openbsd"
            ACTIVATE_CMD=". .venv/bin/activate"
            OS_LABEL="OpenBSD $(uname -r 2>/dev/null || printf 'unknown')"
            PYTHON_INSTALL="doas pkg_add python3 py3-pip"
            TESSERACT_INSTALL="doas pkg_add tesseract"
            OPENCV_INSTALL=""
            ;;
        NetBSD)
            OS_FAMILY="netbsd"
            ACTIVATE_CMD=". .venv/bin/activate"
            OS_LABEL="NetBSD $(uname -r 2>/dev/null || printf 'unknown')"
            if have pkgin; then
                PYTHON_INSTALL="sudo pkgin install python311 py311-pip"
                TESSERACT_INSTALL="sudo pkgin install tesseract"
            else
                PYTHON_INSTALL="sudo pkg_add python311 py311-pip"
                TESSERACT_INSTALL="sudo pkg_add tesseract"
            fi
            OPENCV_INSTALL=""
            ;;
        Darwin)
            OS_FAMILY="macos"
            OS_LABEL="macOS $(sw_vers -productVersion 2>/dev/null || printf 'unknown')"
            PYTHON_INSTALL="brew install python"
            TESSERACT_INSTALL="brew install tesseract"
            OPENCV_INSTALL=""
            ;;
        *)
            unsupported_os "$OS_KERNEL"
            ;;
    esac
}

show_platform_notes() {
    case "$OS_FAMILY" in
        alpine)
            section "Alpine notes"
            info "Alpine uses musl libc. NumPy and OpenCV may need to build from source."
            if [ -n "$BUILD_TOOLS_INSTALL" ]; then
                info "If pip cannot find a wheel, install the build packages with:"
                info "  $BUILD_TOOLS_INSTALL"
            fi
            info "The Docker/Podman image avoids native musl builds when a runtime is available."
            ;;
        freebsd|dragonfly|openbsd|netbsd)
            section "BSD notes"
            info "BSD support is best-effort. PyPI wheels for NumPy, Pillow, and OpenCV"
            info "may not exist for your release, so pip may build them from source."
            info "FreeBSD can use Podman; OpenBSD, NetBSD, and DragonFly are easiest"
            info "with the native Python environment. See INSTALL.md for details."
            info "The base BSD shell does not support 'source'; activate with:"
            info "  . .venv/bin/activate"
            ;;
    esac
}

print_container_optional_build() {
    runtime="$1"
    compose_command="$2"
    info "Optional Tesseract languages and script fonts are build arguments."
    info "For German, Chinese, Hindi, and Kannada support, rebuild with:"
    if [ -n "$compose_command" ]; then
        info "  TESSERACT_LANGS=\"eng deu chi_sim chi_tra hin kan\" \\"
        info "  EXTRA_FONTS=\"fonts-noto-cjk fonts-noto-core\" \\"
        info "  $compose_command"
    else
        info "  $runtime build \\"
        info "      --build-arg TESSERACT_LANGS=\"eng deu chi_sim chi_tra hin kan\" \\"
        info "      --build-arg EXTRA_FONTS=\"fonts-noto-cjk fonts-noto-core\" \\"
        info "      -t inkpatch ."
        info "Then run the $runtime run command above."
    fi
    info "See INSTALL.md for the full list of language and font packages."
}

show_container_install_instructions() {
    dnf_command="dnf"

    have dnf || dnf_command="yum"

    section "Docker/Podman"
    info "Neither Docker nor Podman was detected; the native OS install can continue."
    info "To use containers instead, install a runtime first:"
    case "$OS_FAMILY" in
        debian)
            info "  Docker:"
            info "    sudo apt update && sudo apt install -y docker.io docker-compose"
            info "    sudo systemctl enable --now docker"
            info '    sudo usermod -aG docker "$USER"   # log out and back in afterwards'
            info "  Podman:"
            info "    sudo apt update && sudo apt install -y podman podman-compose"
            ;;
        fedora)
            if is_rhel_like; then
                info "  Podman (available on RHEL-family systems):"
                info "    sudo $dnf_command install -y podman podman-compose"
                info "    sudo $dnf_command install -y epel-release   # if podman-compose is unavailable"
                info "  Docker: install Docker Engine from your distribution or vendor repository:"
                info "    https://docs.docker.com/engine/install/"
            else
                info "  Podman (recommended on Fedora):"
                info "    sudo $dnf_command install -y podman podman-compose"
                info "  Docker: install Docker Engine from Docker's Fedora repository:"
                info "    https://docs.docker.com/engine/install/fedora/"
            fi
            ;;
        arch)
            info "  Docker:"
            info "    sudo pacman -S --needed docker docker-compose"
            info "    sudo systemctl enable --now docker"
            info '    sudo usermod -aG docker "$USER"   # log out and back in afterwards'
            info "  Podman:"
            info "    sudo pacman -S --needed podman podman-compose"
            ;;
        opensuse)
            info "  Docker:"
            info "    sudo zypper install -y docker docker-compose"
            info "    sudo systemctl enable --now docker"
            info '    sudo usermod -aG docker "$USER"   # log out and back in afterwards'
            info "  Podman:"
            info "    sudo zypper install -y podman podman-compose"
            ;;
        alpine)
            info "  Docker:"
            info "    sudo apk add docker docker-cli-compose"
            info "    sudo rc-update add docker default"
            info "    sudo service docker start"
            info "  Podman:"
            info "    sudo apk add podman podman-compose"
            ;;
        freebsd)
            info "  Podman (Docker is not native on FreeBSD):"
            info "    sudo pkg install -y podman"
            info "    sudo service podman enable"
            info "  The podman-suite package adds extra tooling."
            ;;
        openbsd)
            info "OpenBSD does not provide Docker or Podman."
            info "Use the native install, or run the container on another Linux host."
            ;;
        netbsd)
            info "Podman is available in pkgsrc but runs Linux containers through a VM:"
            info "  sudo pkgin install podman"
            info "The native install is recommended on NetBSD."
            ;;
        dragonfly)
            info "Docker and Podman are not native on DragonFly BSD."
            info "Use the native install, or run the container on another host."
            ;;
        macos)
            info "  Docker Desktop:"
            info "    brew install --cask docker"
            info "    open -a Docker"
            info "  Podman:"
            info "    brew install podman"
            info "    podman machine init"
            info "    podman machine start"
            ;;
    esac
    info "The installer never installs these packages or builds containers."
    info "See INSTALL.md for the full container instructions."
}

print_docker_commands() {
    section "Docker run commands"
    info "The installer does not build or start containers automatically."
    info "Run:"
    info "  cd \"$ROOT\""
    if have docker && docker compose version >/dev/null 2>&1; then
        info "  docker compose up --build -d"
        print_container_optional_build docker "docker compose up --build -d"
    elif have docker-compose; then
        info "  docker-compose up --build -d"
        print_container_optional_build docker "docker-compose up --build -d"
    else
        info "  docker build -t inkpatch ."
        info "  docker run --rm -p 127.0.0.1:8000:8000 -v inkpatch-data:/data inkpatch"
        info "Install the Docker Compose plugin if you prefer 'docker compose'."
        print_container_optional_build docker ""
    fi
    info "Open http://127.0.0.1:8000 after the container starts."
}

print_podman_commands() {
    section "Podman run commands"
    info "The installer does not build or start containers automatically."
    info "Run:"
    info "  cd \"$ROOT\""
    if have podman-compose; then
        info "  podman-compose up --build -d"
        print_container_optional_build podman "podman-compose up --build -d"
    elif have podman && podman compose version >/dev/null 2>&1; then
        info "  podman compose up --build -d"
        print_container_optional_build podman "podman compose up --build -d"
    else
        info "  podman build -t inkpatch ."
        info "  podman run --rm -p 127.0.0.1:8000:8000 -v inkpatch-data:/data inkpatch"
        info "Install a Podman Compose provider if you prefer 'podman compose'."
        print_container_optional_build podman ""
    fi
    info "Open http://127.0.0.1:8000 after the container starts."
}

choose_install_method() {
    docker_ok=0
    podman_ok=0
    answer=""

    have docker && docker_ok=1
    have podman && podman_ok=1
    if [ "$docker_ok" -eq 0 ] && [ "$podman_ok" -eq 0 ]; then
        show_container_install_instructions
        return 0
    fi

    section "Installation method"
    info "A container runtime was detected."
    [ "$docker_ok" -eq 1 ] && info "  Docker: $(command -v docker)"
    [ "$podman_ok" -eq 1 ] && info "  Podman: $(command -v podman)"
    if [ ! -t 0 ]; then
        info "No interactive terminal is available; using the native OS install."
        return 0
    fi
    info "Choose how to install InkPatch:"
    info "  n) Native OS install (Python virtual environment)"
    [ "$docker_ok" -eq 1 ] && info "  d) Docker"
    [ "$podman_ok" -eq 1 ] && info "  p) Podman"

    while :; do
        printf 'Choose [n/d/p] (default n): '
        if ! read -r answer; then
            info ""
            info "No input; using the native OS install."
            return 0
        fi
        case "${answer:-n}" in
            n|N|native|Native)
                return 0
                ;;
            d|D|docker|Docker)
                if [ "$docker_ok" -eq 1 ]; then
                    print_docker_commands
                    exit 0
                fi
                ;;
            p|P|podman|Podman)
                if [ "$podman_ok" -eq 1 ]; then
                    print_podman_commands
                    exit 0
                fi
                ;;
        esac
        info "Enter n, d, or p."
    done
}

find_python() {
    PYTHON_BIN=""
    PYTHON_VERSION=""
    PYTHON_FOUND_VERSION=""
    candidate=""
    version=""

    for candidate in python3 python3.13 python3.12 python3.11 python3.10 python; do
        have "$candidate" || continue
        version="$("$candidate" -c 'import platform; print(platform.python_version())' 2>/dev/null || true)"
        [ -n "$version" ] && PYTHON_FOUND_VERSION="$version"
        if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; then
            PYTHON_BIN="$(command -v "$candidate")"
            PYTHON_VERSION="$version"
            return 0
        fi
    done
    return 1
}

show_python_instructions() {
    section "Python"
    info "Python 3.10 or newer is not installed."
    if [ -n "$PYTHON_FOUND_VERSION" ]; then
        info "The Python found is $PYTHON_FOUND_VERSION, which is too old."
    fi
    if [ "$OS_FAMILY" = "macos" ] && ! have brew; then
        info "Homebrew is not installed. Install it first:"
        info '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    fi
    info "Install Python with:"
    info "  $PYTHON_INSTALL"
    case "$OS_FAMILY" in
        freebsd|dragonfly|openbsd|netbsd)
            info "Run the package command as root (with sudo or doas as configured)."
            ;;
    esac
    info "Then run this installer again."
    exit 1
}

python_dependencies_ok() {
    [ -x "$VENV_PY" ] || return 1
    "$VENV_PY" - <<'PY' >/dev/null 2>&1
import importlib.util
import sys

required = ["fastapi", "uvicorn", "pydantic", "PIL", "numpy", "cv2"]
if any(importlib.util.find_spec(name) is None for name in required):
    raise SystemExit(1)
if (
    importlib.util.find_spec("multipart") is None
    and importlib.util.find_spec("python_multipart") is None
):
    raise SystemExit(1)

try:
    import fastapi  # noqa: F401
    import numpy  # noqa: F401
    import PIL  # noqa: F401
    import pydantic  # noqa: F401
    import uvicorn  # noqa: F401
    import cv2  # noqa: F401
except Exception:
    raise SystemExit(1)
PY
}

show_python_build_hints() {
    if [ -n "$BUILD_TOOLS_INSTALL" ]; then
        info "The source build may need these packages:"
        info "  $BUILD_TOOLS_INSTALL"
    fi
    case "$OS_FAMILY" in
        freebsd|dragonfly|openbsd|netbsd)
            info "PyPI wheels may be unavailable on BSD. If pip could not build a"
            info "package, install the native package or use the container route."
            info "See INSTALL.md for the per-BSD notes."
            ;;
    esac
}

install_python_dependencies() {
    section "Python dependencies"
    if python_dependencies_ok && "$VENV_PY" -m pip check >/dev/null 2>&1; then
        info "Python dependencies are already installed."
        return 0
    fi

    if [ -x "$VENV_PY" ]; then
        info "Python dependencies are not installed in .venv (or the environment is incomplete)."
    else
        info "The Python virtual environment is not installed."
    fi
    info "Installing with:"
    info "  python3 -m venv .venv"
    info "  $ACTIVATE_CMD"
    info "  python -m pip install -r requirements.txt"

    if ! "$PYTHON_BIN" -m venv "$VENV_DIR"; then
        info "Could not create .venv."
        info "Install the venv and pip packages with:"
        info "  $PYTHON_INSTALL"
        info "If .venv is broken, remove it and rerun this script:"
        info "  rm -rf .venv"
        exit 1
    fi

    # shellcheck disable=SC1091
    if ! . "$VENV_DIR/bin/activate"; then
        info "Could not activate .venv."
        exit 1
    fi
    if ! python -m pip install -r "$REQUIREMENTS"; then
        info "Python dependency installation failed."
        show_python_build_hints
        info "Check your internet connection or proxy, then run:"
        info "  $ACTIVATE_CMD"
        info "  python -m pip install -r requirements.txt"
        exit 1
    fi
    if ! python_dependencies_ok; then
        info "Python packages were installed, but they could not be imported."
        if ! "$VENV_PY" -c 'import cv2' >/dev/null 2>&1 && [ -n "$OPENCV_INSTALL" ]; then
            info "OpenCV may be missing system libraries. Install them with:"
            info "  $OPENCV_INSTALL"
        fi
        show_python_build_hints
        exit 1
    fi
}

check_tesseract() {
    TESSERACT_STATE="missing"
    TESSERACT_BIN=""
    if have tesseract; then
        TESSERACT_BIN="$(command -v tesseract)"
        if "$TESSERACT_BIN" --version >/dev/null 2>&1; then
            if "$TESSERACT_BIN" --list-langs 2>&1 | grep -qx 'eng'; then
                TESSERACT_STATE="ok"
            else
                TESSERACT_STATE="no-eng"
            fi
        fi
    fi
}

check_optional_support() {
    section "Optional language and font support"
    OPTIONAL_STATUS=0
    if [ ! -f "$ROOT/scripts/check_optional.py" ]; then
        info "scripts/check_optional.py is missing; cannot check optional language/font support."
        info "See INSTALL.md for the Noto font and extra Tesseract language packages."
        OPTIONAL_STATUS=1
        return 0
    fi
    if [ -n "$TESSERACT_BIN" ]; then
        TESSERACT_CMD="$TESSERACT_BIN" "$VENV_PY" "$ROOT/scripts/check_optional.py" --family "$OS_FAMILY"
    else
        "$VENV_PY" "$ROOT/scripts/check_optional.py" --family "$OS_FAMILY"
    fi
    OPTIONAL_STATUS=$?
    return 0
}

show_tesseract_instructions() {
    if [ "$TESSERACT_STATE" = "missing" ]; then
        info "Tesseract OCR is not installed."
        info "Install it with:"
        info "  $TESSERACT_INSTALL"
    else
        info "Tesseract is installed, but the English language data (eng) is missing."
        info "Install the English data with:"
        info "  $TESSERACT_INSTALL"
    fi
    if [ "$OS_FAMILY" = "macos" ] && ! have brew; then
        info "Homebrew is not installed. Install it first:"
        info '  /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    fi
    info "If Tesseract is installed elsewhere, set TESSERACT_CMD before starting InkPatch."
    info "Additional OCR languages are listed in INSTALL.md."
}

show_summary_and_run_command() {
    section "Summary"
    if [ "$TESSERACT_STATE" = "ok" ]; then
        if [ "${OPTIONAL_STATUS:-1}" -eq 0 ]; then
            success "Everything is installed."
        else
            info "Everything required is installed."
            warn "Optional language/font support is incomplete; see the section above."
        fi
    else
        info "InkPatch is not fully installed."
        show_tesseract_instructions
    fi

    section "Run InkPatch"
    info "  cd \"$ROOT\""
    info "  $ACTIVATE_CMD"
    info "  python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
    info "Then open http://127.0.0.1:8000"

    if [ "$TESSERACT_STATE" != "ok" ]; then
        info "OCR will be unavailable until Tesseract is installed."
        exit 1
    fi
}

setup_colours
print_icon
printf '%sProject: %s%s\n' "$COLOR_DIM" "$ROOT" "$COLOR_RESET"

[ -f "$REQUIREMENTS" ] || fail "requirements.txt was not found next to this script."

detect_platform
printf 'OS: %s\n' "$OS_LABEL"

choose_install_method
show_platform_notes

if ! find_python; then
    show_python_instructions
fi
info "Python: $PYTHON_BIN ($PYTHON_VERSION)"

if ! "$PYTHON_BIN" -m venv --help >/dev/null 2>&1; then
    section "Python venv"
    info "Python is installed, but its venv module is missing."
    info "Install it with:"
    info "  $PYTHON_INSTALL"
    exit 1
fi

check_tesseract
case "$TESSERACT_STATE" in
    ok)
        info "Tesseract: $TESSERACT_BIN (English data available)"
        ;;
    missing)
        info "Tesseract: not installed"
        ;;
    no-eng)
        info "Tesseract: $TESSERACT_BIN (English data missing)"
        ;;
esac

install_python_dependencies
check_optional_support
show_summary_and_run_command
