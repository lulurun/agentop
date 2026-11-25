import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { ApiError } from "../../api/client";
import { Section } from "../../components/Section";
import type { PluginContext } from "../types";
import { chosenAfter, latestOnly, pick, progressPath, stateLabel, when, type Choice, type NoteRow, type Progress, type TaskRow } from "./progress";

const Markdown = lazy(() => import("./Markdown"));

type Load = { kind: "loading" } | { kind: "ready" } | { kind: "error"; message: string; gone: boolean };

function Doc({ text }: { text: string }) {
  return (
    <Suspense fallback={<pre className="progress-text">{text}</pre>}>
      <Markdown text={text} />
    </Suspense>
  );
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="progress-field">
      <h5>{label}</h5>
      {children}
    </div>
  );
}

function Multiline({ text }: { text: string | null | undefined }) {
  return text ? <p className="progress-text">{text}</p> : null;
}

function TaskDetail({ row }: { row: TaskRow }) {
  const last = row.rounds.length;
  return (
    <div className="progress-detail">
      <Field label="Input">
        {row.input ? (
          <>
            <Multiline text={row.input.goal} />
            {row.input.accept.length > 0 && (
              <ul>
                {row.input.accept.map((c, i) => (
                  <li key={i} className="progress-text">{c}</li>
                ))}
              </ul>
            )}
            <p className="progress-meta">Artifact: <code>{row.input.artifact || "—"}</code></p>
            {row.input.brief && <p className="progress-meta progress-text">Brief: {row.input.brief}</p>}
          </>
        ) : (
          <p className="detail-empty">Unavailable: no record of this task's input survives.</p>
        )}
      </Field>
      <Field label="Output">
        {row.rounds.length === 0 && <p className="detail-empty">No rounds recorded.</p>}
        {row.rounds.map((r) => (
          <div key={r.round} className="progress-round">
            <span className="progress-meta">
              Round {r.round}
              {r.reported_at != null && ` · ${when(r.reported_at)}`}
              {r.blocked && " · blocked"}
              {r.artifact_present === false && " · artifact missing then"}
            </span>
            {r.summary ? <Multiline text={r.summary} /> : <p className="detail-empty">{row.input ? "No report." : "Unavailable."}</p>}
          </div>
        ))}
      </Field>
      <Field label="Evaluation">
        {row.rounds.filter((r) => r.feedback).map((r) => (
          <div key={r.round} className="progress-round">
            <span className="progress-meta">Round {r.round} rejected</span>
            <Multiline text={r.feedback} />
          </div>
        ))}
        {row.status === "accepted" && (
          <div className="progress-round">
            <span className="progress-meta">Round {last} accepted</span>
            <Multiline text={row.checked} />
          </div>
        )}
        {row.status === "failed" && (
          <div className="progress-round">
            <span className="progress-meta">Round {last} failed</span>
            <Multiline text={row.note} />
          </div>
        )}
        {row.status === "open" && <p className="detail-empty">Not decided yet.</p>}
      </Field>
      {row.raw && (
        <Field label="Recorded entry">
          <pre className="progress-text progress-raw">{row.raw}</pre>
        </Field>
      )}
    </div>
  );
}

function Task({ row, open, onToggle }: { row: TaskRow; open: boolean; onToggle(): void }) {
  const tool = [row.tool, row.model && `(${row.model})`].filter(Boolean).join(" ");
  const time = row.status === "open" ? `Opened ${when(row.opened_at, row.opened_approx)}` : when(row.closed_at, row.closed_approx);
  return (
    <li className="progress-task" data-state={row.state}>
      <button type="button" className="progress-row" aria-expanded={open} onClick={onToggle}>
        <span className="progress-row-head">
          <span className="progress-id">{row.id}</span>
          <span className="progress-state">{stateLabel(row)}</span>
        </span>
        <span className="progress-meta">
          {row.unlinked ? "Unlinked" : row.action} · {row.session ?? "unknown session"}
          {tool && ` · ${tool}`}
        </span>
        <span className="progress-meta">{time}</span>
      </button>
      {open && <TaskDetail row={row} />}
    </li>
  );
}

function Note({ note }: { note: NoteRow }) {
  return (
    <li className="progress-task">
      <div className="progress-note">
        <span className="progress-row-head">
          <span className="progress-id">{note.action} note</span>
          <span className="progress-state">by {note.by}</span>
        </span>
        <span className="progress-meta">{note.recorded_at != null ? when(note.recorded_at, true) : note.recorded || "Unknown time"}</span>
        <Multiline text={note.body} />
      </div>
    </li>
  );
}

function Execution({ data, expanded, toggle }: { data: Progress; expanded: Set<string>; toggle(id: string): void }) {
  if (!data.active.length && !data.completed.length && !data.notes.length) {
    return <p className="detail-empty">{data.missing.executions ? "No execution recorded for this direction yet." : "No tasks or notes yet."}</p>;
  }
  const list = (title: string, rows: TaskRow[]) =>
    rows.length > 0 && (
      <>
        <h4 className="progress-group">{title} ({rows.length})</h4>
        <ul className="progress-list">
          {rows.map((row) => (
            <Task key={row.id} row={row} open={expanded.has(row.id)} onToggle={() => toggle(row.id)} />
          ))}
        </ul>
      </>
    );
  return (
    <>
      {list("Active", data.active)}
      {list("Completed, newest first", data.completed)}
      {data.notes.length > 0 && (
        <>
          <h4 className="progress-group">Notes ({data.notes.length})</h4>
          <ul className="progress-list">
            {data.notes.map((note) => (
              <Note key={note.id} note={note} />
            ))}
          </ul>
        </>
      )}
    </>
  );
}

/** The Progress Viewer of one project's track. */
export function ProgressViewer({ plugin, project }: { plugin: PluginContext; project: string }) {
  const [data, setData] = useState<Progress | null>(null);
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  const [choice, setChoice] = useState<Choice>({ follow: true, generation: null });
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());
  const requests = useMemo(latestOnly, []);
  const choiceRef = useRef(choice);
  choiceRef.current = choice;

  const fetchFor = useCallback(
    async (wanted: Choice) => {
      const current = requests.begin();
      setLoad({ kind: "loading" });
      try {
        const response = await plugin.client.get<Progress>(progressPath(project, wanted.follow ? null : wanted.generation));
        if (!current() || response.project !== project) return;
        setData(response);
        setChoice(chosenAfter(wanted, response));
        setLoad({ kind: "ready" });
      } catch (error) {
        if (!current()) return;
        const gone = error instanceof ApiError && error.status === 404;
        if (gone) setData(null);
        setLoad({ kind: "error", gone, message: gone ? "This project is no longer there." : error instanceof Error ? error.message : String(error) });
      }
    },
    [plugin, project, requests],
  );

  useEffect(() => {
    void fetchFor({ follow: true, generation: null });
    return () => requests.cancel();
  }, [fetchFor, requests]);

  const refresh = () => void fetchFor(choiceRef.current);
  const toggle = (id: string) =>
    setExpanded((old) => {
      const next = new Set(old);
      if (!next.delete(id)) next.add(id);
      return next;
    });

  const loading = load.kind === "loading";
  const failed = load.kind === "error" ? load : null;
  return (
    <div className="progress-viewer" aria-busy={loading}>
      <div className="progress-toolbar">
        {data && data.generations.length > 1 && (
          <label className="progress-pick">
            <span>Direction</span>
            <select
              value={data.generation}
              onChange={(event) => {
                const next = pick(Number(event.target.value), data.current);
                setChoice(next);
                void fetchFor(next);
              }}
            >
              {data.generations.map((n) => (
                <option key={n} value={n}>
                  {n === data.current ? `${n} (current)` : n}
                </option>
              ))}
            </select>
          </label>
        )}
        <button type="button" onClick={refresh} disabled={loading}>
          {loading && data ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {loading && !data && <p className="detail-empty" role="status">Loading the track…</p>}
      {failed && (
        <div className="progress-error" role="alert">
          <p className="form-error">
            {data ? `Couldn't refresh, so this may be out of date: ${failed.message}` : failed.message}
          </p>
          {!failed.gone && !data && (
            <button type="button" onClick={refresh}>
              Retry
            </button>
          )}
        </div>
      )}
      {data && data.issues.length > 0 && (
        <ul className="progress-issues" aria-label="Unreadable records">
          {data.issues.map((issue) => (
            <li key={issue}>{issue}</li>
          ))}
        </ul>
      )}
      {data && data.missing.track && (
        <p className="detail-empty">No track yet{data.legacy ? ": this project keeps its state outside track/." : "."}</p>
      )}
      {data && !data.missing.track && (
        <>
          <Section id={`projects:progress:${project}:direction`} title={`Direction ${data.generation}`}>
            {data.direction == null ? (
              <p className="detail-empty">DIRECTION.md is missing from track/{data.generation}.</p>
            ) : (
              <>
                {data.direction_placeholder && <p className="detail-empty">The direction isn't written yet.</p>}
                <Doc text={data.direction} />
              </>
            )}
          </Section>
          <Section id={`projects:progress:${project}:plan`} title="Plan">
            {data.plan == null ? <p className="detail-empty">PLAN.md is missing from track/{data.generation}.</p> : <Doc text={data.plan} />}
          </Section>
          <Section id={`projects:progress:${project}:execution`} title="Execution">
            <Execution data={data} expanded={expanded} toggle={toggle} />
          </Section>
        </>
      )}
    </div>
  );
}
