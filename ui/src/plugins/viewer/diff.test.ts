import { describe, expect, it } from "vitest";
import { parseDiff } from "./diff";

const MODIFIED = `diff --git a/a.txt b/a.txt
index 1191247..2b2f2e1 100644
--- a/a.txt
+++ b/a.txt
@@ -1,3 +10,3 @@ def f():
 one
-two
+deux
 three
\\ No newline at end of file
`;

describe("parseDiff", () => {
  it("numbers the old and new lines of each hunk", () => {
    const diff = parseDiff(MODIFIED);
    expect(diff.added).toBe(1);
    expect(diff.removed).toBe(1);
    expect(diff.notes).toEqual([]);
    expect(diff.rows).toEqual([
      { kind: "hunk", text: "@@ -1,3 +10,3 @@ def f():" },
      { kind: "context", old: 1, new: 10, text: "one" },
      { kind: "del", old: 2, new: null, text: "two" },
      { kind: "add", old: null, new: 11, text: "deux" },
      { kind: "context", old: 3, new: 12, text: "three" },
      { kind: "note", text: "No newline at end of file" },
    ]);
  });

  it("keeps what git says about the whole file, not the --- and +++ lines", () => {
    const diff = parseDiff("diff --git a/n b/n\nnew file mode 100644\nindex 0..1\n--- /dev/null\n+++ b/n\n@@ -0,0 +1 @@\n+x\n");
    expect(diff.notes).toEqual(["new file mode 100644"]);
    expect(diff.rows[1]).toEqual({ kind: "add", old: null, new: 1, text: "x" });
  });

  it("reads a binary change as a note with no rows", () => {
    const diff = parseDiff("diff --git a/b b/b\nindex 1..2 100644\nBinary files a/b and b/b differ\n");
    expect(diff.notes).toEqual(["Binary files a/b and b/b differ"]);
    expect(diff.rows).toEqual([]);
  });
});
