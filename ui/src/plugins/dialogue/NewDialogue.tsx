/**
 * The New dialogue wizard, in a tab of the Dialogues feature. Pick a
 * scenario, fill in each field of its brief's template, pick the two agents,
 * and the service makes the dialogue's folder, saves the brief, and starts it.
 */
import { useEffect, useMemo, useState, type FormEvent } from "react";
import type { ToolInfo } from "../../api/client";
import { useApp } from "../../store";
import type { PluginContext } from "../types";
import { closeWizard, refreshDialogues, selectDialogue, type Scenario } from "./state";

/** `acceptance_criteria` → `Acceptance criteria`. */
export function fieldLabel(name: string): string {
  const words = name.replace(/_+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Single-line fields; the rest get a box of several lines. */
const ONE_LINE = new Set(["title", "name"]);

export function NewDialogue({ plugin }: { plugin: PluginContext }) {
  const act = useApp((s) => s.act);
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [scenarioId, setScenarioId] = useState("");
  // Kept across scenarios, so a field two of them share keeps its answer.
  const [values, setValues] = useState<Record<string, string>>({});
  const [agentA, setAgentA] = useState("");
  const [agentB, setAgentB] = useState("");
  const [maxTurns, setMaxTurns] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let live = true;
    plugin.client.get<Scenario[]>("/scenarios").then(
      (found) => live && setScenarios(found),
      (err: unknown) => live && (setScenarios([]), setError(err instanceof Error ? err.message : String(err))),
    );
    void act((client) => client.tools()).then((found) => {
      if (!live || !found) return;
      const installed = found.filter((t) => t.installed);
      setTools(installed);
      const first = installed[0]?.name ?? "";
      setAgentA((current) => current || first);
      setAgentB((current) => current || first);
    });
    return () => {
      live = false;
    };
  }, [plugin.client, act]);

  const scenario = useMemo(() => scenarios?.find((s) => s.id === scenarioId), [scenarios, scenarioId]);
  const main = scenarios?.filter((s) => !s.shelf) ?? [];
  const shelf = scenarios?.filter((s) => s.shelf) ?? [];

  function pick(id: string) {
    setScenarioId(id);
    setError("");
    const next = scenarios?.find((s) => s.id === id);
    setMaxTurns(next ? String(next.max_turns) : "");
  }

  const missing = scenario ? scenario.fields.filter((f) => !values[f]?.trim()) : [];
  const turns = Number(maxTurns);
  const ready = !!scenario && missing.length === 0 && !!agentA && !!agentB && Number.isInteger(turns) && turns >= 1 && turns <= 500;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!scenario || !ready || submitting) return;
    setSubmitting(true);
    setError("");
    try {
      const answers = Object.fromEntries(scenario.fields.map((f) => [f, values[f].trim()]));
      const created = await plugin.client.post<{ id: string }>("/dialogues", {
        scenario: scenario.id,
        values: answers,
        agent_a: agentA,
        agent_b: agentB,
        max_turns: turns,
      });
      await refreshDialogues(plugin.client);
      closeWizard();
      selectDialogue(created.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(false);
    }
  }

  return (
    <section className="view dialogue-wizard" aria-label="New dialogue">
      <form onSubmit={(event) => void submit(event)}>
        <h2>New dialogue</h2>
        <label className="field">
          Scenario
          <select required value={scenarioId} onChange={(event) => pick(event.target.value)} disabled={scenarios === null} data-autofocus>
            <option value="" disabled>
              {scenarios === null ? "Loading…" : "Choose a scenario"}
            </option>
            {main.length > 0 && (
              <optgroup label="Scenarios">
                {main.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </optgroup>
            )}
            {shelf.length > 0 && (
              <optgroup label="Older scenarios">
                {shelf.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </optgroup>
            )}
          </select>
        </label>

        {scenario && (
          <>
            <div className="dialogue-scenario">
              {scenario.summary && <p>{scenario.summary}</p>}
              <p className="view-row-meta">
                Agent A is <strong>{scenario.name_a}</strong>, agent B is <strong>{scenario.name_b}</strong>.
              </p>
              <details>
                <summary>The brief these fields fill in</summary>
                <pre className="dialogue-text">{scenario.description.trim()}</pre>
              </details>
            </div>

            <h3>Brief</h3>
            <p className="field-hint">Every field is required.</p>
            {scenario.fields.map((field) => (
              <label key={field} className="field">
                {fieldLabel(field)}
                {ONE_LINE.has(field) ? (
                  <input
                    type="text"
                    required
                    value={values[field] ?? ""}
                    onChange={(event) => setValues({ ...values, [field]: event.target.value })}
                  />
                ) : (
                  <textarea
                    required
                    rows={4}
                    value={values[field] ?? ""}
                    onChange={(event) => setValues({ ...values, [field]: event.target.value })}
                  />
                )}
              </label>
            ))}

            <h3>Agents</h3>
            <div className="dialogue-agents">
              <label className="field">
                {scenario.name_a} (A)
                <select required value={agentA} onChange={(event) => setAgentA(event.target.value)}>
                  {tools.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                {scenario.name_b} (B)
                <select required value={agentB} onChange={(event) => setAgentB(event.target.value)}>
                  {tools.map((t) => (
                    <option key={t.name} value={t.name}>
                      {t.name}
                    </option>
                  ))}
                </select>
              </label>
              <label className="field">
                Max turns
                <input type="number" required min={1} max={500} value={maxTurns} onChange={(event) => setMaxTurns(event.target.value)} />
              </label>
            </div>
          </>
        )}

        {error && <p className="form-error">{error}</p>}
        <div className="dialog-actions">
          <button type="button" onClick={closeWizard}>
            Cancel
          </button>
          <button type="submit" className="primary" disabled={!ready || submitting} title={missing.length ? `Still to fill in: ${missing.map(fieldLabel).join(", ")}` : undefined}>
            {submitting ? "Starting…" : "Start dialogue"}
          </button>
        </div>
      </form>
    </section>
  );
}
