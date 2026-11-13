/** A unified diff from `git diff`, split into rows for the diff view. */

export type DiffRow =
  | { kind: "hunk"; text: string }
  | { kind: "context" | "add" | "del"; old: number | null; new: number | null; text: string }
  | { kind: "note"; text: string };

export interface ParsedDiff {
  /** What git says about the file as a whole: new, deleted, renamed, mode changes. */
  notes: string[];
  rows: DiffRow[];
  added: number;
  removed: number;
}

const HUNK = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/;
const HEADER_NOTES = /^(new file mode|deleted file mode|old mode|new mode|similarity index|rename from|rename to|Binary files)/;

export function parseDiff(text: string): ParsedDiff {
  const notes: string[] = [];
  const rows: DiffRow[] = [];
  let added = 0;
  let removed = 0;
  let oldLine = 0;
  let newLine = 0;
  let inHunk = false;
  const lines = text.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  for (const line of lines) {
    const hunk = HUNK.exec(line);
    if (hunk) {
      inHunk = true;
      oldLine = Number(hunk[1]);
      newLine = Number(hunk[2]);
      rows.push({ kind: "hunk", text: line });
    } else if (!inHunk || line.startsWith("diff --git ")) {
      inHunk = false;
      if (HEADER_NOTES.test(line)) notes.push(line);
    } else if (line.startsWith("+")) {
      added += 1;
      rows.push({ kind: "add", old: null, new: newLine++, text: line.slice(1) });
    } else if (line.startsWith("-")) {
      removed += 1;
      rows.push({ kind: "del", old: oldLine++, new: null, text: line.slice(1) });
    } else if (line.startsWith("\\")) {
      rows.push({ kind: "note", text: line.slice(2) });
    } else {
      rows.push({ kind: "context", old: oldLine++, new: newLine++, text: line.slice(1) });
    }
  }
  return { notes, rows, added, removed };
}
