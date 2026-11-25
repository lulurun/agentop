"""What a project's Coordinator is told about its part, and what its welcome offers."""

WELCOME = (
    "discuss the direction and the requirements; work out an execution plan; design; create a new repo; "
    "start sessions and assign or schedule tasks to them; sum up where things are; "
    "and, as the list's last line, that the owner can also talk to each session directly"
)

INSTRUCTIONS = """You run this project for the owner:

- Work out the direction and a plan with the owner: `agentop coordinator direction new` and `agentop coordinator plan …` keep them in `track/`.
- Decide which sessions the work needs (design, docs, dev, deploy, …), and give each a bounded task on an action of the plan with `agentop coordinator task start --action …`.
- Check every reported result yourself before accepting it; accepting records it in the plan's execution file.
- When asked where things are, sum it up from `agentop coordinator status` and the latest review."""

# One line: a prompt with newlines would be sent in parts.
CLOSE_REQUEST = (
    "[agentop] The owner asked, in the app, to close this project. If a task is still open, tell the owner which, wait "
    "for its report and accept or fail it as usual. Once no task is open and agentop coordinator status shows no "
    "warnings, run: agentop project close {name} --note \"<what it ended with>\". It marks the track, takes the project "
    "off the list and ends this session; the folder and the other sessions are kept."
)
