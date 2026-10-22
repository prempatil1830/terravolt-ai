#!/usr/bin/env bash
# run_all.sh — Rebuilds results from raw cache and config, runs tests, and regenerates reporting.
set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================================================"
echo "Karnataka Solar PV — Full Pipeline Rebuild & Verification (REPLAY Mode)"
echo "================================================================================"

# Use virtualenv python if available, else system python
if [ -f "./tenv/bin/python" ]; then
    PYTHON="./tenv/bin/python"
    PYTEST="./tenv/bin/pytest"
else
    PYTHON="python3"
    PYTEST="pytest"
fi

export REPLAY=1
export PYTHONPATH="$SCRIPT_DIR:$SCRIPT_DIR/geoprocessing:$SCRIPT_DIR/backend"

echo ""
echo "[Step 1/5] Running full candidate selection under Frozen Rule v2 (from raw cache)..."
$PYTHON geoprocessing/generate_candidates.py

echo ""
echo "[Step 2/5] Synchronizing MongoDB and provenance records..."
$PYTHON backend/migrate_to_mongo.py

echo ""
echo "[Step 3/5] Running automated test suite in REPLAY mode..."
$PYTEST tests/ -v

echo ""
echo "[Step 4/5] Running sensitivity analysis..."
$PYTHON geoprocessing/sensitivity_analysis.py

echo ""
echo "[Step 5/5] Regenerating final report package (final_report.md)..."
$PYTHON geoprocessing/final_report.py

echo ""
echo "================================================================================"
echo "Pipeline execution finished cleanly with all tests passing in REPLAY mode."
echo "================================================================================"

