"""Entry point: ``python -m agent_db.bench run <cases> [options]``.

Typer (0.21) flattens a single subcommand onto the top level, so
``agent_db.bench.cli`` accepts ``cli <cases>`` directly.  This module
accepts an optional leading ``run`` token (ТЗ §9 compatibility) and
delegates to the same CLI.
"""

from __future__ import annotations

import sys


def main() -> None:
    args = sys.argv[1:]
    # Allow both `python -m agent_db.bench run <cases>` and
    # `python -m agent_db.bench <cases>` (drop a leading "run").
    if args and args[0] == "run":
        args = args[1:]
    sys.argv = [sys.argv[0]] + args

    from .cli import app

    app()


if __name__ == "__main__":
    main()
