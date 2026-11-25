# Direct communication with agent tools

A proposal for investigation, not a description of the current implementation. agentop currently runs agents in tmux and communicates through their terminals ([0_00_design.md](0_00_design.md)).

## Direction

Investigate running agent tools through their structured interfaces instead of controlling their interactive terminals through tmux. agentop would start an agent process, send it prompts and control requests, receive events as it works, and show the conversation in its own interface. The agent tool would still do the work and handle its own authentication, configuration, skills and permissions. agentop would not implement a coding agent itself.

Today agentop starts each agent in tmux, displays its terminal, and sends prompts by pasting text and pressing Enter. Hooks, terminal state and tool records help it understand what the agent is doing. This keeps the agent's own interface and lets sessions run independently of the service, but it also makes agentop dependent on terminal behavior: prompt readiness, screen contents, key sequences, redraws and differences between CLI versions.

[Paseo](https://github.com/getpaseo/paseo) offers a useful example: it communicates with Codex through `codex app-server` and uses the Claude Agent SDK with an installed Claude Code executable. These are starting points for investigation, not yet choices for agentop. Antigravity and future tools need their own supported integration paths.

## What this could improve

- **Prompts and handoffs.** Send a message as a message, without pasting into a prompt whose state must be inferred. The owner's draft and another agent's handoff stay separate.
- **Session state.** Receive turn and tool events where the provider exposes them, reducing reliance on hooks and screen interpretation to tell whether an agent is working, finished or waiting.
- **Conversation and permissions.** Show streamed replies, tool calls, questions and approval requests as structured content, with controls suited to desktop and phone.
- **Coordinator and dialogues.** Let a Coordinator observe and direct sessions through explicit events and messages. Dialogues could collect replies without extracting them from a terminal screen.
- **History and context.** Use provider session IDs and structured conversation data where available, reducing the need to discover the active conversation from process and screen state.
- **Mobile use.** Text entry, scrolling and selection would no longer depend on a terminal emulator for directly integrated sessions.

A structured interface does not guarantee correct status, durable history or a clean answer for every interaction. The improvements depend on what each provider exposes and how reliably it does so.

## What must be preserved or resolved

- **Session lifetime.** tmux currently keeps agents running when the app closes or the service restarts. Determine how directly managed processes survive service restarts, or how sessions are safely recovered. Resuming a conversation is not necessarily the same as preserving an in-progress turn.
- **Subscription and authentication.** Verify the actual authentication path, billing and applicable provider terms for each integration and account type. A chat interface alone does not establish whether subscription usage is available.
- **Agent capabilities.** Check approvals, questions, interruption, model changes, attachments, skills and tool output. Make limitations visible when a provider does not expose a capability.
- **Existing sessions.** Decide how managed tmux sessions and sessions started outside agentop remain visible and usable. Do not assume an existing interactive process can be attached through a structured protocol.
- **Provider differences.** Define a common session interface for what agentop needs, while allowing provider-specific capabilities and failures to remain visible.
- **Reliability.** Test against real installed agent versions, including disconnects, service restarts, provider upgrades, permission requests and interrupted turns.

## First investigation

Build a small Codex app-server prototype using the user's installed Codex: start a session, send a prompt, stream its response, handle an approval and an interrupt, and resume the conversation. Test authentication and billing, and what happens when the agentop service stops during a turn.

Use the results to decide whether direct communication can become agentop's main session runtime, what should remain available through tmux, and how the same approach might work for Claude and Antigravity. This would change the current design's explicit choice to leave the interface to the terminal; session lifetime and provider behavior are central to that decision.
