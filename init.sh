#!/usr/bin/env bash
# init.sh — verify the repo is ready for work
# Exits 0 if all checks pass, 1 otherwise.
# Linux/bash only (no bashisms beyond standard POSIX).

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT" || { echo "FATAL: cannot cd to $REPO_ROOT"; exit 1; }

echo "== init.sh: verifying $REPO_ROOT =="

# 1. .env exists check (no values printed)
if [ ! -f .env ]; then
  echo "WARN: .env not present (skipping LLM_PRIMARY_API_KEY check)"
else
  # presence only, never print value
  if grep -q '^LLM_PRIMARY_API_KEY=.\\+' .env 2>/dev/null; then
    echo "OK: .env has LLM_PRIMARY_API_KEY (value hidden)"
  else
    echo "WARN: .env exists but LLM_PRIMARY_API_KEY empty/missing"
  fi
fi

# 2. pytest -q (with timeout to avoid hangs)
echo "== pytest -q =="
if command -v python3 >/dev/null 2>&1; then
  PY=python3
elif command -v python >/dev/null 2>&1; then
  PY=python
else
  echo "FATAL: python not found"
  exit 1
fi

# 120s budget for pytest
if ! timeout 120 "$PY" -m pytest tests/ -q; then
  echo "FATAL: pytest failed (or timed out)"
  exit 1
fi

# 3. py_compile on app/
echo "== py_compile =="
PY_FILES=$(find app -name "*.py" 2>/dev/null)
if [ -z "$PY_FILES" ]; then
  echo "WARN: no .py files found in app/"
else
  if ! "$PY" -m py_compile $PY_FILES; then
    echo "FATAL: py_compile failed"
    exit 1
  fi
fi

# 4. JSON validation of feature_list.json (silent unless broken)
if [ -f feature_list.json ]; then
  if ! "$PY" -c "import json; json.load(open('feature_list.json'))" 2>/dev/null; then
    echo "FATAL: feature_list.json is not valid JSON"
    exit 1
  fi
  echo "OK: feature_list.json valid"
fi

echo ""
echo "== Repo ready =="
echo "Next step: see session-handoff.md for current state and next action."
