"""Entry point: ``python -m agent_db.bench run <cases> [options]``."""

from __future__ import annotations


def main() -> None:
    from .cli import app

    app()


if __name__ == "__main__":
    main()
