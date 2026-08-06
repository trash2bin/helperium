"""API layer for the university assistant demo."""


def main() -> None:
    """Run the demo API server."""
    from api_service.server import main as server_main

    server_main()


__all__ = ["main"]
