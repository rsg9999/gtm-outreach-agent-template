#!/usr/bin/env bash
# Minimal installer. Assumes you've already cloned your template copy and `cd`-ed into it.
#
# Usage:
#   git clone https://github.com/<your-username>/gtm-outreach-agent.git
#   cd gtm-outreach-agent
#   bash install.sh

set -euo pipefail

echo "=================================="
echo " GTM Outreach Agent — install"
echo "=================================="

# 1. Verify we're in the right place
if [[ ! -f "pyproject.toml" ]] || ! grep -q "gtm-outreach-agent" pyproject.toml; then
  echo "Error: run this from the repo root (no pyproject.toml found here)." >&2
  exit 1
fi

# 2. Install uv if missing
if ! command -v uv >/dev/null 2>&1; then
  echo "uv not found. Installing..."
  if command -v brew >/dev/null 2>&1; then
    brew install uv
  else
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # shellcheck source=/dev/null
    [[ -f "${HOME}/.cargo/env" ]] && . "${HOME}/.cargo/env"
  fi
fi
echo "  uv: $(uv --version)"

# 3. Sync deps
echo ""
echo "Installing Python dependencies..."
uv sync

# 4. Hand off to the interactive setup
echo ""
echo "Dependencies installed. Launching guided setup..."
echo ""
uv run apply init
