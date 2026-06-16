# AgentOp Dialogue Scenarios

This directory contains the active scenario workflow. Older general-purpose
scenarios live under `shelf/`.

## Active Workflow

1. `product-direction.toml`
   - High-level product direction: job-to-be-done, source strategy, and
     prioritization or scoring philosophy.
   - Produces `product-strategy.md`, `source-strategy.md`, and
     `scoring-model.md`.

2. `implementation-requirements.toml` *(default)*
   - Converts approved product direction into implementation requirements.
   - Produces `requirements.md`, `data-contract.md`, `api-ui-plan.md`,
     `implementation-tasks.md`, and `test-plan.md`.

3. `implementation-handoff.toml`
   - Optional final handoff review before giving work to a single code agent.
   - Produces `code-agent-brief.md` and `execution-checklist.md`.
   - Skip this scenario when requirements are already clear enough.

## Research Workflow

4. `strategy-discovery.toml`
   - Both agents explore the Makalu codebase (`/home/lulurun/workspace/Makalu/`),
     the data warehouse (`/panda/makalu-data/`), and the existing orion strategy
     (`/home/lulurun/workspace/potential-panda-orion/`) before producing any
     deliverable. No pre-written data description needed — agents discover it.
   - StrategyExplorer proposes a repeatable discovery workflow; WorkflowCritic
     verifies by reading the same sources and challenges executability,
     look-ahead controls, and quality bar.
   - Produces `ecosystem-map.md`, `discovery-workflow.md`, and
     `hypothesis-backlog.md`.
   - Pair with `briefs/trading-strategy-discovery.md`.

## Completion Protocol

Each scenario requires a review loop. Role B should approve with a domain
specific `STATUS: ..._SHIPPABLE` while using delimiter `status:continue`, so
Role A gets the final turn and declares the scenario-specific ready marker.
