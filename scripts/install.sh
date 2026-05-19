#!/usr/bin/env sh
set -eu

REPO_URL="${CHAPPE_REPO_URL:-https://github.com/crimeacs/chappe}"
SPEC="git+${REPO_URL}"

say() {
  printf '%s\n' "$*"
}

if command -v uv >/dev/null 2>&1; then
  say "Installing Chappe with uv..."
  UV_ERR="$(mktemp)"
  if uv tool install --quiet "$SPEC" 2>"$UV_ERR"; then
    grep -v '^WARN ' "$UV_ERR" >&2 || true
  else
    cat "$UV_ERR" >&2
    rm -f "$UV_ERR"
    exit 1
  fi
  rm -f "$UV_ERR"
elif command -v pipx >/dev/null 2>&1; then
  say "Installing Chappe with pipx..."
  pipx install --quiet "$SPEC"
else
  PYTHON_BIN="${PYTHON:-python3}"
  INSTALL_ROOT="${CHAPPE_INSTALL_ROOT:-$HOME/.local/share/chappe/tool}"
  BIN_DIR="${CHAPPE_BIN_DIR:-$HOME/.local/bin}"
  say "Installing Chappe into a private venv at $INSTALL_ROOT..."
  "$PYTHON_BIN" -m venv "$INSTALL_ROOT"
  "$INSTALL_ROOT/bin/python" -m pip install --upgrade pip
  "$INSTALL_ROOT/bin/python" -m pip install "$SPEC"
  mkdir -p "$BIN_DIR"
  ln -sf "$INSTALL_ROOT/bin/chappe" "$BIN_DIR/chappe"
  case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) say "Add $BIN_DIR to PATH if 'chappe' is not found in new shells." ;;
  esac
fi

if command -v chappe >/dev/null 2>&1; then
  say "Chappe installed. Running bootstrap diagnostics..."
  if [ -n "${CHAPPE_CHANNEL:-}" ]; then
    chappe --pretty bootstrap "$CHAPPE_CHANNEL" || chappe --pretty onboard --channel "$CHAPPE_CHANNEL"
  else
    chappe --pretty bootstrap || chappe --pretty onboard
  fi
else
  say "Chappe installed, but the 'chappe' command is not on PATH yet."
  say "Try: $HOME/.local/bin/chappe --pretty bootstrap"
fi
