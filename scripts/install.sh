#!/bin/bash
set -euo pipefail
# Sets ClipForge up on this Mac (SPEC 20): checks for what it needs, installs its Python and web parts,
# downloads the models, runs doctor and builds ClipForge.app, all as you, never as an administrator.
# With --check it only checks, and says how to get anything missing.

cd "$(dirname "${BASH_SOURCE[0]}")/.."

missing=0
need() {  # what is missing, then how to get it
  echo "Missing: $1. $2"
  missing=1
}

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
  echo "ClipForge needs a Mac with Apple silicon (M1 or later)."
  exit 1
fi
command -v brew >/dev/null || need "Homebrew" "Install it from https://brew.sh, then open a new Terminal window."
command -v ffmpeg >/dev/null || need "ffmpeg" "Install it with: brew install ffmpeg"
command -v uv >/dev/null || need "uv" "Install it with: brew install uv"
if command -v node >/dev/null; then
  node_version="$(node --version)"
  major="${node_version#v}"
  major="${major%%.*}"
  if ! [[ "$major" =~ ^[0-9]+$ ]] || ((major < 22)); then
    need "Node.js 22 or later (this Mac has $node_version)" "Update it with: brew upgrade node"
  fi
else
  need "Node.js 22 or later" "Install it with: brew install node"
fi
if [[ ! -d "/Applications/Google Chrome.app" && ! -d "$HOME/Applications/Google Chrome.app" ]]; then
  need "Google Chrome" "Download it from https://www.google.com/chrome and drag it to Applications."
fi
if ((missing)); then
  echo "Install what's missing, then run scripts/install.sh again."
  exit 1
fi
if ! { xcode-select -p >/dev/null 2>&1 && [[ -x "${SWIFTC:-/usr/bin/swiftc}" ]]; }; then
  # Not needed: without it a notification still comes, but clicking it can't open ClipForge's page.
  echo "Note: this Mac has no Swift compiler, so clicking a ClipForge notification won't open its page."
  echo "Get it with: xcode-select --install"
fi
echo "Everything ClipForge needs is here."
if [[ "${1:-}" == "--check" ]]; then
  exit 0
fi

echo "Installing ClipForge's Python parts..."
uv sync --frozen
echo "Building ClipForge's window..."
npm --prefix web ci --ignore-scripts
npm --prefix web run build
echo "Downloading the models once (the speech model is about 2.5 GB)..."
.venv/bin/clipforge assets fetch
if ! .venv/bin/clipforge doctor; then
  echo "ClipForge is installed, but doctor found something to fix (above). Run .venv/bin/clipforge doctor after fixing it."
fi
scripts/make_app.sh
echo "Done. Open ClipForge from Applications in your home folder, or with Spotlight."
