#!/usr/bin/env bash
set -euo pipefail

PREFIX="${HOME}/.local"
SOURCE=""

usage() {
    cat <<EOF
Usage: $0 --prefix <path> <REPO-URL|local-path>

Install kubux-calendar from git or local directory.

Examples:
  $0 --prefix ~/.local https://gitlab.kubux.net/kubux/programming/programs/kubux-calendar.git
  $0 --prefix ~/.local .

Options:
  --prefix <path>   Installation prefix (default: \$HOME/.local)
  -h, --help        Show this help
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --prefix) PREFIX="$2"; shift 2 ;;
        -h|--help) usage ;;
        *) SOURCE="$1"; shift ;;
    esac
done

[[ -n "$SOURCE" ]] || { echo "ERROR: No source specified." >&2; usage; }

if [[ -d "$SOURCE" ]]; then
    INSTALL_SRC="$(cd "$SOURCE" && pwd)"
elif [[ "$SOURCE" =~ ^https?:// || "$SOURCE" =~ ^git@ ]]; then
    TMPDIR="$(mktemp -d)"
    git clone --depth=1 "$SOURCE" "$TMPDIR"
    INSTALL_SRC="$TMPDIR"
else
    echo "ERROR: '$SOURCE' is neither a directory nor a git URL." >&2; exit 1
fi

BINDIR="${PREFIX}/bin"
VENVDIR="${PREFIX}/lib/kubux-calendar/venv"
APPDIR="${PREFIX}/share/applications"
LIBDIR="${PREFIX}/lib/kubux-calendar"

mkdir -p "$BINDIR" "$APPDIR" "$LIBDIR"

# --- Python virtualenv ---
echo "--- Setting up Python virtualenv ---"
if python3 -m venv --help 2>/dev/null | grep -q -- --without-pip; then
    python3 -m venv --without-pip "$VENVDIR"
else
    python3 -m venv "$VENVDIR" 2>/dev/null || {
        python3 -m virtualenv "$VENVDIR" 2>/dev/null || {
            pip3 install --user virtualenv 2>/dev/null || true
            python3 -m virtualenv "$VENVDIR" 2>/dev/null || true
        }
    }
fi
source "${VENVDIR}/bin/activate"

if ! python3 -m pip --version &>/dev/null; then
    echo "  Installing pip ..."
    curl -sL https://bootstrap.pypa.io/get-pip.py | python3
fi

echo "  Installing Python dependencies ..."
python3 -m pip install --quiet --upgrade pip
python3 -m pip install --quiet \
    PySide6 requests caldav ics icalendar \
    pytz python-dateutil recurring-ical-events

deactivate
echo "  Python virtualenv ready."

# --- Install source ---
echo "--- Installing source ---"
cp "$INSTALL_SRC/kubux_calendar.py" "$LIBDIR/"
cp -r "$INSTALL_SRC/backend" "$LIBDIR/"
cp -r "$INSTALL_SRC/gui" "$LIBDIR/"
cp -r "$INSTALL_SRC/library" "$LIBDIR/"
find "$LIBDIR" -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# --- Create wrapper ---
echo "Creating wrapper script ..."
cat > "${BINDIR}/kubux-calendar" <<WRAP
#!/usr/bin/env bash
export TMPDIR="\${TMPDIR:-/tmp}"
export PYTHONPATH="${LIBDIR}"
exec "${VENVDIR}/bin/python" "${LIBDIR}/kubux_calendar.py" "\$@"
WRAP
chmod +x "${BINDIR}/kubux-calendar"

echo "  Installed to $BINDIR/kubux-calendar"

# --- Desktop file ---
DESKTOP_SRC="${INSTALL_SRC}/kubux-calendar.desktop"
if [[ -f "$DESKTOP_SRC" ]]; then
    sed -e "s|^Exec=kubux-calendar|Exec=${BINDIR}/kubux-calendar|" \
        "$DESKTOP_SRC" > "${APPDIR}/kubux-calendar.desktop"
    echo "  Desktop file installed to $APPDIR"
fi

[[ -n "${TMPDIR:-}" ]] && rm -rf "$TMPDIR"

echo "============================================"
echo "Installation complete!"
echo "  Prefix: $PREFIX"
echo "  Binaries: $BINDIR"
echo "  Add to PATH: export PATH=\"\$PATH:${BINDIR}\""
echo "============================================"