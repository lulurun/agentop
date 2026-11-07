"""Entry point kept for units that still run `python src/api.py`. Use `agentop serve`."""

from agentop.service.app import serve

if __name__ == "__main__":
    serve()
