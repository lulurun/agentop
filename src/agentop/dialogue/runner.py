"""Entry point for the background dialogue orchestrator subprocess."""
import sys


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: runner.py <dialogue_id>", file=sys.stderr)
        sys.exit(1)

    dialogue_id = sys.argv[1]

    from agentop.dialogue.orchestrator import DialogueOrchestrator
    orch = DialogueOrchestrator(dialogue_id)
    orch.start()
    orch.join()  # block until the dialogue finishes or is stopped


if __name__ == "__main__":
    main()
