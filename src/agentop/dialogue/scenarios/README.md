# AgentOp Dialogue Scenarios

A scenario defines a reusable **interaction style** between two roles.
The brief supplies the topic, the sources to explore, and the domain-specific
criteria. Scenarios and briefs are designed to be mixed: any scenario can run
with any brief that fits its interaction style.

Older or domain-specific scenarios live under `shelf/`.

---

## Product development workflow

These three scenarios chain together: direction → requirements → handoff.

### 1. `product-direction.toml`

Style: ProductStrategist debates with ProductCritic.

ProductStrategist defines product direction (job-to-be-done, source strategy,
prioritisation philosophy). ProductCritic challenges scope, signal logic, and
false-positive controls.

Deliverables: `product-strategy.md`, `source-strategy.md`, `scoring-model.md`

Completion: `PRODUCT_DIRECTION_READY` / `STATUS: DIRECTION_SHIPPABLE`

---

### 2. `implementation-requirements.toml` *(default)*

Style: ProductOwner collaborates with TechnicalArchitect.

ProductOwner translates approved product direction into implementation
requirements. TechnicalArchitect challenges feasibility, data contracts,
scoring specificity, and test coverage.

Deliverables: `requirements.md`, `data-contract.md`, `api-ui-plan.md`,
`implementation-tasks.md`, `test-plan.md`

Completion: `REQUIREMENTS_READY` / `STATUS: REQUIREMENTS_SHIPPABLE`

---

### 3. `implementation-handoff.toml`

Style: ImplementationLead prepares a handoff, CodeReviewer audits it.

Optional final check before handing work to a single code agent. Skip when
requirements are already unambiguous.

Deliverables: `code-agent-brief.md`, `execution-checklist.md`

Completion: `HANDOFF_READY` / `STATUS: HANDOFF_SHIPPABLE`

---

## Research / discovery

### 4. `explorer-critic.toml`

Style: Explorer discovers, Critic verifies by reading the same sources.

The Explorer reads every source the brief specifies before drafting anything,
then produces the deliverables the brief defines. The Critic independently
reads the same sources and challenges groundedness, completeness,
executability, and the domain-specific criteria listed in the brief.

**All domain-specific content lives in the brief** — paths to explore,
deliverable names and descriptions, challenge criteria, and acceptance
criteria. The scenario is reusable across topics.

Example brief: `briefs/trading-strategy-discovery.md`

Completion: `WORK_READY` / `STATUS: WORK_APPROVED`

---

## Completion protocol

Each scenario requires a review loop. Role B approves with a domain-specific
`STATUS: ..._SHIPPABLE` (or `STATUS: WORK_APPROVED` for explorer-critic)
while using delimiter `status:continue`, so Role A gets the final turn and
declares the scenario-specific ready marker using `status:complete`.
