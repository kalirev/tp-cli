#!/bin/sh
# Install tp as a real command (needed for output capture: terminals exec it).
#
#   sh install.sh            -> ~/.local/bin/tp, runs this checkout directly
#
# No pip, no venv: tp is stdlib-only, so the launcher just points python3 at
# this folder. `git pull` here upgrades it in place.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
bin="${TP_BIN_DIR:-$HOME/.local/bin}"
py=$(command -v python3)
mkdir -p "$bin"
cat > "$bin/tp" <<EOF
#!/bin/sh
# tp launcher (written by $here/install.sh)
PYTHONPATH="$here\${PYTHONPATH:+:\$PYTHONPATH}" exec "$py" -m tp.cli "\$@"
EOF
chmod 755 "$bin/tp"
echo "installed: $bin/tp -> $here ($("$bin/tp" --version))"

case ":$PATH:" in
  *":$bin:"*) ;;
  *) echo "note: $bin is not on PATH. fish:  fish_add_path $bin" ;;
esac

if command -v fish >/dev/null 2>&1; then
  if fish -c 'functions -q tp' 2>/dev/null; then
    echo "note: a fish function named 'tp' also exists ($(fish -c 'functions --details tp' 2>/dev/null))."
    echo "      it can stay (it runs the same code), or remove it:  functions -e tp; funcsave tp"
  fi
fi
echo "next: open a NEW terminal, then run:  tp doctor"
