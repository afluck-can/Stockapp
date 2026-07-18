#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if command -v uv &>/dev/null; then
  uv pip install --system -q -r requirements.txt
else
  pip install -q -r requirements.txt
fi
cd api
python index.py
