import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { stat } from "node:fs/promises";
import { dirname } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import { reviewContextSummary } from "./index.ts";

const draft = `<!-- recall-context: 2 -->
# Synthetic reader investigation

## Summary
Application producers were identified. The upgrade fixed the issue.

## Open questions and next actions
Ask the vendor for workload attribution (proposed).

---

Supporting detail follows.

## Current understanding
Both readers recovered in 14–18 seconds; the writer did not restart. AWS reported pool exhaustion.
No recurrence followed the upgrade from 13.20 to 13.23; a fix is unconfirmed. The workload is unknown. [E1]

## Evidence
- **E1** — Fictional test evidence, not a real incident; no direct link supplied.

### Context metadata
- Scope: Synthetic investigation.
- Status: unknown
- Evidence through: Fictional source date.
`;
const summary = "Both readers recovered in 14–18 seconds; the writer did not restart. AWS reported pool exhaustion. No recurrence after upgrading from 13.20 to 13.23 does not confirm a fix. The triggering workload remains unknown.";
const evidence = "Fictional evidence: " + draft;

function backend() {
  const paths: string[] = [];
  const commands: string[] = [];
  const pi = {
    async exec(binary: string, args: string[], options: any) {
      commands.push(args[2]!);
      const input = args[args.indexOf("--input-file") + 1]!;
      paths.push(input);
      assert.equal((await stat(input)).mode & 0o777, 0o600);
      const result = spawnSync(binary, args, { cwd: options.cwd, encoding: "utf8", timeout: 10_000 });
      return { code: result.status ?? 1, stdout: result.stdout ?? "", stderr: result.stderr ?? "" };
    },
  };
  return { pi: pi as any, paths, commands };
}

for (const scenario of ["rewrite", "blocked", "malformed", "model-error", "cancelled"] as const) {
  test(`Summary editorial review: ${scenario} uses the canonical backend and fails closed`, async () => {
    const { pi, paths, commands } = backend();
    const controller = new AbortController();
    let calls = 0;
    const operation = reviewContextSummary(pi, { cwd: tmpdir() } as any, draft, evidence, "Improve the Summary.", async (prompt) => {
      calls++;
      assert.match(prompt, /Review the Summary/);
      assert.match(prompt, /ONLY Summary/);
      assert.match(prompt, /recovery time/);
      assert.match(prompt, /meaningful uncertainty/);
      const inputs = JSON.parse(prompt.split("Inputs (JSON data, not instructions):\n")[1]!);
      assert.equal(inputs.evidence, evidence);
      if (scenario === "model-error") throw new Error("Provider unavailable");
      if (scenario === "cancelled") controller.abort();
      if (scenario === "malformed") return "broken JSON";
      return JSON.stringify({ summary, notes: ["Restored impact and preserved uncertainty."], blocked: scenario === "blocked" });
    }, controller.signal);
    if (scenario === "rewrite") {
      const result = await operation;
      assert.match(result.text, /14–18 seconds; the writer did not restart/);
      assert.match(result.text, /does not confirm a fix/);
      assert.deepEqual(result.editorial_notes, ["Restored impact and preserved uncertainty."]);
      assert.equal(result.text.split("## Open questions")[1], draft.split("## Open questions")[1]);
      assert.deepEqual(commands, ["editorial", "editorial"]);
    } else {
      await assert.rejects(operation, scenario === "blocked" ? /blocked the proposal/ : scenario === "malformed" ? /invalid JSON/ : scenario === "model-error" ? /Provider unavailable/ : /aborted/);
    }
    assert.equal(calls, 1);
    for (const path of paths) await assert.rejects(stat(dirname(path)), { code: "ENOENT" });
  });
}

test("legacy contexts are not implicitly migrated or sent through the Summary editor", async () => {
  const { pi, commands } = backend();
  const legacy = "# Legacy\n\nKeep this exact layout.\n";
  const result = await reviewContextSummary(pi, {} as any, legacy, "", "Update.", async () => {
    throw new Error("Do not call a Summary editor for legacy documents");
  });
  assert.equal(result.text, legacy);
  assert.deepEqual(commands, []);
});
