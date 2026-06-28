#!/usr/bin/env bash
# Wrapper around run.py. Passes all args through, e.g. ./run.sh --dev
cd "$(dirname "$0")" || exit 1
exec "${PYTHON:-python}" run.py "$@"
