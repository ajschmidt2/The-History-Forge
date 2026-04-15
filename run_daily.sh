#!/bin/bash
# run_daily.sh — Run the History Forge daily video job.
# Usage: ./run_daily.sh
# Output is appended to logs/daily_YYYYMMDD.log

set -euo pipefail

cd "$(dirname "$0")"

LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/daily_$(date +%Y%m%d).log"

# Activate virtual environment if present
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

echo "[$(date)] Starting History Forge daily job" | tee -a "$LOG_FILE"
python3 -m src.workflow.daily_job 2>&1 | tee -a "$LOG_FILE"
echo "[$(date)] Done" | tee -a "$LOG_FILE"
