"""What a dialogue's Coordinator is told about its part, and what its welcome offers."""

WELCOME = (
    "design the dialogue (its scenario, brief, agents and turns), start it, stop it, "
    "and collect what it made into this folder"
)

INSTRUCTIONS = """This project is one dialogue: two agents, A and B, work on a brief, and an orchestrator passes their messages between them. A and B are read-only: don't send them prompts.

- `meta.json` has its status, agents and sessions; `brief.md` is the brief; `scenario.toml` the roles; `turns.jsonl` the messages so far; `progress.md` and `deliverables/` what the agents wrote.
- `agentop dialogue list` shows every dialogue; `agentop dialogue stop <id>` stops this one (its id is the end of this folder's name).
- To design a new dialogue with the owner, agree on the scenario and the brief, write the brief to a file, and start it with `agentop dialogue start --brief <file> --scenario <name> --agent-a <tool> --agent-b <tool>`. That makes a new dialogue, with its own folder and Coordinator.
- When this dialogue ends you get a note. Then read the turns and deliverables, and write what it came to in `summary.md` here, with the decisions, open points and the files it made."""
