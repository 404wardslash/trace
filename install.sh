#!/usr/bin/env bash
set -euo pipefail

# ── sudo guard ────────────────────────────────────────────────────────────────
# Running with sudo would install trace into root's home directory, not yours.
# The script calls sudo itself for apt-get when it needs to.
if [[ -n "${SUDO_USER:-}" ]]; then
  echo "Error: do not run this script with sudo." >&2
  echo "" >&2
  echo "  ./install.sh" >&2
  echo "" >&2
  echo "The installer calls sudo automatically for system packages (apt-get)." >&2
  echo "Running it as root would install trace into /root instead of your home." >&2
  exit 1
fi
# ─────────────────────────────────────────────────────────────────────────────

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
METHOD="pipx"
DEV="0"
SKIP_APT="0"
SKIP_ZSH_PROMPT="0"

usage() {
  cat <<'EOF'
Trace installer

Usage:
  ./install.sh [options]          # do NOT prefix with sudo

Options:
  --pipx           Install with pipx (default, best for normal use)
  --venv           Install into .venv and symlink trace into ~/.local/bin
  --dev            Include test dependencies
  --no-apt         Do not try to install system packages with apt
  --no-zsh-prompt  Skip adding the trace prompt integration to ~/.zshrc
  -h, --help       Show this help

After install:
  source ~/.zshrc        # pick up PATH + prompt changes in this terminal
  trace init oscp-lab
  trace web
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --pipx)
      METHOD="pipx"
      shift
      ;;
    --venv)
      METHOD="venv"
      shift
      ;;
    --dev)
      DEV="1"
      shift
      ;;
    --no-apt)
      SKIP_APT="1"
      shift
      ;;
    --no-zsh-prompt)
      SKIP_ZSH_PROMPT="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 2
      ;;
  esac
done

have() {
  command -v "$1" >/dev/null 2>&1
}

need_python() {
  if ! have python3; then
    echo "python3 is required." >&2
    if [[ "$SKIP_APT" == "0" ]] && have sudo && have apt-get; then
      sudo apt-get update
      sudo apt-get install -y python3 python3-venv python3-pip
    else
      echo "Install python3, python3-venv, and python3-pip, then rerun ./install.sh." >&2
      exit 1
    fi
  fi
}

ensure_local_bin() {
  mkdir -p "$HOME/.local/bin"
  local line='export PATH="$HOME/.local/bin:$PATH"'
  local profiles=()

  case "${SHELL:-}" in
    */zsh) profiles+=("$HOME/.zshrc") ;;
    */bash) profiles+=("$HOME/.bashrc") ;;
  esac

  [[ -f "$HOME/.zshrc" ]] && profiles+=("$HOME/.zshrc")
  [[ -f "$HOME/.bashrc" ]] && profiles+=("$HOME/.bashrc")
  profiles+=("$HOME/.profile")

  local wrote="0"
  local profile
  for profile in "${profiles[@]}"; do
    [[ -n "$profile" ]] || continue
    if [[ ! -f "$profile" ]]; then
      touch "$profile"
    fi
    if ! grep -Fqx "$line" "$profile"; then
      {
        echo ""
        echo "# Trace CLI"
        echo "$line"
      } >> "$profile"
      wrote="1"
    fi
    break
  done

  export PATH="$HOME/.local/bin:$PATH"

  if [[ "$wrote" == "1" ]]; then
    echo "Added ~/.local/bin to your shell startup file."
  fi
}

install_pipx() {
  need_python
  if ! have pipx; then
    if [[ "$SKIP_APT" == "0" ]] && have sudo && have apt-get; then
      sudo apt-get update
      sudo apt-get install -y pipx
    else
      python3 -m pip install --user pipx
    fi
  fi

  python3 -m pipx ensurepath >/dev/null || true
  if [[ "$DEV" == "1" ]]; then
    pipx install --force --editable "$PROJECT_DIR[test]"
  else
    pipx install --force --editable "$PROJECT_DIR"
  fi
  ensure_local_bin
}

install_venv() {
  need_python
  python3 -m venv "$PROJECT_DIR/.venv"
  # shellcheck disable=SC1091
  source "$PROJECT_DIR/.venv/bin/activate"
  python -m pip install --upgrade pip
  if [[ "$DEV" == "1" ]]; then
    python -m pip install -e "$PROJECT_DIR[test]"
  else
    python -m pip install -e "$PROJECT_DIR"
  fi
  ensure_local_bin
  ln -sfn "$PROJECT_DIR/.venv/bin/trace" "$HOME/.local/bin/trace"
}

setup_zsh_prompt() {
  local zshrc="$HOME/.zshrc"

  if [[ "$SKIP_ZSH_PROMPT" == "1" ]]; then
    return
  fi

  if [[ ! -f "$zshrc" ]]; then
    return
  fi

  if grep -q "trace prompt" "$zshrc"; then
    echo "Trace prompt already configured — skipping."
    return
  fi

  # Append a precmd hook at the end of .zshrc so it runs after any
  # theme/plugin that sets PROMPT, capturing the final template there.
  cat >> "$zshrc" <<'TRACE_PROMPT_BLOCK'

# ── Trace prompt integration ──────────────────────────────────────────────────
# Replaces ㉿hostname with the active engagement/target in your Kali zsh prompt.
autoload -Uz add-zsh-hook
_trace_set_prompt() {
  local _tp
  _tp="$(trace prompt 2>/dev/null)"
  PROMPT="${_TRACE_PROMPT_TPL/㉿%m/㉿${_tp:-$(hostname -s)}}"
}
[[ -z "$_TRACE_PROMPT_TPL" ]] && _TRACE_PROMPT_TPL="$PROMPT"
add-zsh-hook precmd _trace_set_prompt
# ─────────────────────────────────────────────────────────────────────────────
TRACE_PROMPT_BLOCK

  echo "Trace prompt integration added to $zshrc"
}

case "$METHOD" in
  pipx) install_pipx ;;
  venv) install_venv ;;
  *) echo "Invalid method: $METHOD" >&2; exit 2 ;;
esac

setup_zsh_prompt

echo
echo "Trace installed."
if have trace; then
  trace --help >/dev/null
  echo "Verified: trace --help works."
else
  echo "trace is installed but not on your current PATH yet."
fi
echo
echo "Run this to activate everything in the current terminal:"
echo "  source ~/.zshrc"
echo
echo "Then get started:"
echo "  trace init oscp-lab"
echo "  trace web"
