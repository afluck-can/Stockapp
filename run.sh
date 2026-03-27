#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/backend"
# Install dependencies (uv is faster and handles build issues better than pip)
if command -v uv &>/dev/null; then
  uv pip install --system -q -r requirements.txt
else
  pip install -q -r requirements.txt
fi
python app.py
