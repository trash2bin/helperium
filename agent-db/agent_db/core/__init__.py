"""agent-db core utilities."""

import os
from pathlib import Path

# Resolve project root - look for marker file or use env var
if "AGENT_TUTOR_ROOT" in os.environ:
    PROJECT_ROOT = Path(os.environ["AGENT_TUTOR_ROOT"])
else:
    # Fallback: look for AGENTS.md at project root
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / "AGENTS.md").exists():
            PROJECT_ROOT = parent
            break
    else:
        # Last resort: assume standard layout
        PROJECT_ROOT = current.parent.parent.parent.parent

SCENARIOS_DIR = PROJECT_ROOT / "data-service" / "testdata" / "scenarios"
DATA_SERVICE_URL = "http://127.0.0.1:8084"
ADMIN_TOKEN = "secret"
