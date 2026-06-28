#!/usr/bin/env bash
# Wrapper around install.py. Passes args through, e.g. ./install.sh --backend
cd "$(dirname "$0")" || exit 1
exec "${PYTHON:-python}" install.py "$@"
