"""Entry point: ``python -m agent_db.stress run <profile> [options]``."""

from __future__ import annotations


def main() -> None:
    from .cli import main as cli_main

    cli_main()


if __name__ == "__main__":
    main()
