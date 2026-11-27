/** The Project creation buffer: a name and the Coordinator's agent tool, in a tab of Projects with none picked. */
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { activeClient, isShown, useApp, type ViewOwner } from "../../store";
import type { PluginContext } from "../types";
import {
  EMPTY_DRAFT,
  FEATURE,
  isCreator,
  listing,
  NAME_PATTERN,
  openProject,
  preferredTool,
  updateDraft,
  useDrafts,
  type Project,
} from "./state";

type Tools = { connection: string; names: string[] } | { connection: string; error: string };

export function NewProject({ plugin }: { plugin: PluginContext }) {
  const service = useApp((s) => s.activeId);
  const draft = useDrafts((s) => s.drafts[service]) ?? EMPTY_DRAFT;
  const focusAsked = useDrafts((s) => s.focusAsked);
  const [tools, setTools] = useState<Tools | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let live = true;
    setTools(null);
    activeClient(useApp.getState())
      .tools()
      .then(
        (found) => live && setTools({ connection: service, names: found.filter((t) => t.installed).map((t) => t.name) }),
        (err: unknown) => live && setTools({ connection: service, error: err instanceof Error ? err.message : String(err) }),
      );
    return () => {
      live = false;
    };
  }, [service]);

  useEffect(() => {
    nameRef.current?.focus();
  }, [focusAsked, service]);

  const installed = tools && tools.connection === service && "names" in tools ? tools.names : null;
  const tool = installed?.includes(draft.tool) ? draft.tool : installed ? preferredTool(installed) : "";
  const wanted = draft.name.trim();
  const badName = wanted !== "" && !NAME_PATTERN.test(wanted);
  const ready = !draft.busy && !!wanted && !badName && !!tool;

  async function submit(event?: FormEvent) {
    event?.preventDefault();
    if (!ready) return;
    // The view and service this began in: a reply that comes after another is shown lands only here.
    const owner: ViewOwner = { connection: service, feature: FEATURE };
    const client = plugin.client;
    updateDraft(owner.connection, { name: wanted, tool, busy: true, error: "" });
    try {
      await client.post<Project>("/projects", { name: wanted, tool });
    } catch (err) {
      updateDraft(owner.connection, { busy: false, error: err instanceof Error ? err.message : String(err) });
      return;
    }
    updateDraft(owner.connection, { name: "", busy: false, error: "" });
    const app = useApp.getState();
    app.closeTabsIn(owner, isCreator);
    if (app.activeId !== owner.connection) return;
    await listing.load(plugin).catch(() => undefined);
    if (isShown(useApp.getState(), owner)) await openProject(owner.connection, wanted, true);
  }

  function onKeyDown(event: KeyboardEvent<HTMLFormElement>) {
    // A select takes Enter itself in no browser's way that submits.
    if (event.key === "Enter" && event.target instanceof HTMLSelectElement) {
      event.preventDefault();
      void submit();
    }
  }

  const loading = tools === null || tools.connection !== service;
  const failed = !loading && "error" in tools ? tools.error : "";
  const none = installed !== null && installed.length === 0;

  return (
    <section className="view project-creator" aria-label="New project">
      <form onSubmit={(event) => void submit(event)} onKeyDown={onKeyDown} aria-busy={draft.busy}>
        <h2>New project</h2>
        <p className="view-row-meta">A project is a folder its sessions share, and a Coordinator you talk to, which starts and routes work to the others.</p>
        <label className="field">
          Name
          <input
            ref={nameRef}
            type="text"
            placeholder="my-project"
            autoComplete="off"
            spellCheck={false}
            maxLength={96}
            value={draft.name}
            disabled={draft.busy}
            aria-invalid={badName || undefined}
            aria-describedby="project-name-hint"
            onChange={(event) => updateDraft(service, { name: event.target.value, error: "" })}
          />
        </label>
        <p id="project-name-hint" className={badName ? "form-error" : "field-hint"}>
          Lowercase letters, digits, '-' and '_', starting with a letter or digit.
        </p>
        <label className="field">
          Coordinator
          <select value={tool} disabled={draft.busy || !installed?.length} onChange={(event) => updateDraft(service, { tool: event.target.value })}>
            {loading && <option value="">Loading…</option>}
            {none && <option value="">No agent tool installed</option>}
            {installed?.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
        {none && <p className="form-error" role="alert">No agent tool is installed on this service, so there's nothing to run the Coordinator with. Install Claude Code, Codex or Antigravity, then come back.</p>}
        {failed && <p className="form-error" role="alert">The installed agent tools couldn't be listed: {failed}</p>}
        {draft.error && <p className="form-error" role="alert">{draft.error}</p>}
        <div className="placard-actions">
          <button type="submit" className="primary" disabled={!ready}>
            {draft.busy ? "Creating…" : "Create"}
          </button>
        </div>
      </form>
    </section>
  );
}
