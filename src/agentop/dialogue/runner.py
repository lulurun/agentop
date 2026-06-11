"""Entry point for the background dialogue orchestrator subprocess."""
import sys


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: runner.py <dialogue_id>", file=sys.stderr)
        sys.exit(1)

    from agentop.dialogue.model import Dialogue
    from agentop.dialogue.orchestrator import DialogueOrchestrator

    d = Dialogue.load(sys.argv[1])
    if not d:
        print(f"Dialogue {sys.argv[1]!r} not found", file=sys.stderr)
        sys.exit(1)

    orch = DialogueOrchestrator(d)
    orch.start()
    orch.join()


if __name__ == "__main__":
    main()
