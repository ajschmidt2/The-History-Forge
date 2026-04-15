#!/bin/bash
# run_compile_only.sh — Re-render the most recent project using existing clips
# and images, skipping all expensive API generation steps.
# Usage: ./run_compile_only.sh [project_id]
#   If project_id is omitted, the most recently modified project is used.

set -euo pipefail

cd "$(dirname "$0")"

LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/compile_$(date +%Y%m%d_%H%M%S).log"

# Activate virtual environment if present
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
elif [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
fi

PROJECT_ID="${1:-}"

echo "[$(date)] Starting compile-only render project=${PROJECT_ID:-<latest>}" | tee -a "$LOG_FILE"

python3 - "$PROJECT_ID" 2>&1 | tee -a "$LOG_FILE" <<'PYEOF'
import sys
from pathlib import Path

project_id = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None

if not project_id:
    # Find the most recently modified project directory
    projects_root = Path("data/projects")
    if not projects_root.exists():
        print("No data/projects directory found.")
        sys.exit(1)
    dirs = sorted(
        [d for d in projects_root.iterdir() if d.is_dir()],
        key=lambda d: d.stat().st_mtime,
        reverse=True,
    )
    if not dirs:
        print("No projects found in data/projects/.")
        sys.exit(1)
    project_id = dirs[0].name
    print(f"Using most recent project: {project_id}")

from src.workflow.services import run_render_video
from src.workflow.models import PipelineOptions

result = run_render_video(project_id, options=PipelineOptions())
print(f"Render result: status={result.status} message={result.message}")
if result.outputs:
    for k, v in result.outputs.items():
        print(f"  {k}: {v}")
PYEOF

echo "[$(date)] Done" | tee -a "$LOG_FILE"
