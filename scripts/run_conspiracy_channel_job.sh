#!/usr/bin/env bash
# ───────────────────────────────────────────────────────────────
# run_conspiracy_channel_job.sh — wrapper for the conspiracy channel daily job
#
# Called by com.historyforge.conspiracy-channel.plist.
# Handles: venv activation, PATH (for ffmpeg), working directory,
#          and a unified log file at data/conspiracy_cron.log.
# ───────────────────────────────────────────────────────────────
set -euo pipefail

PROJECT_DIR="/Users/adamschmidt/The-History-Forge"
VENV_PYTHON="${PROJECT_DIR}/venv/bin/python"
LOG_FILE="${PROJECT_DIR}/data/conspiracy_cron.log"

# Ensure data dir exists for the log
mkdir -p "${PROJECT_DIR}/data"

# Homebrew paths (ffmpeg, etc.) — adjust if using a non-default prefix
export PATH="/opt/homebrew/bin:/usr/local/bin:${PATH}"

# Timestamp helper
ts() { date "+%Y-%m-%d %H:%M:%S"; }

{
    echo "================================================================"
    echo "[$(ts)] Conspiracy channel daily job STARTED"
    echo "================================================================"

    # Preflight checks
    if [[ ! -x "${VENV_PYTHON}" ]]; then
        echo "[$(ts)] ERROR: venv python not found at ${VENV_PYTHON}"
        exit 1
    fi

    if ! command -v ffmpeg &>/dev/null; then
        echo "[$(ts)] ERROR: ffmpeg not found on PATH"
        exit 1
    fi

    echo "[$(ts)] Python: ${VENV_PYTHON}"
    echo "[$(ts)] ffmpeg: $(command -v ffmpeg)"
    echo "[$(ts)] Working dir: ${PROJECT_DIR}"

    cd "${PROJECT_DIR}"

    "${VENV_PYTHON}" -m src.workflow.daily_job --channel conspiracy
    exit_code=$?

    if [[ ${exit_code} -eq 0 ]]; then
        echo "[$(ts)] Conspiracy channel daily job SUCCEEDED (exit ${exit_code})"
    else
        echo "[$(ts)] Conspiracy channel daily job FAILED (exit ${exit_code})"
    fi

    echo ""
} >> "${LOG_FILE}" 2>&1
