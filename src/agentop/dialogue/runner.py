"""Entry point for the background dialogue orchestrator subprocess."""

import logging
import sys


def _setup_logging(log_path) -> None:
    handler = logging.FileHandler(log_path)
    handler.setFormatter(
        logging.Formatter(
            fmt="[%(asctime)s] %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: runner.py <dialogue_id>", file=sys.stderr)
        sys.exit(1)

    from agentop.dialogue.model import Dialogue, DialogueMeta
    from agentop.dialogue.orchestrator import DialogueOrchestrator

    meta = DialogueMeta.load(sys.argv[1])
    if not meta:
        print(f"Dialogue {sys.argv[1]!r} not found", file=sys.stderr)
        sys.exit(1)

    _setup_logging(meta.log_path())

    d = Dialogue.from_meta(meta)
    orch = DialogueOrchestrator(d)
    orch.start()
    orch.join()


if __name__ == "__main__":
    main()
