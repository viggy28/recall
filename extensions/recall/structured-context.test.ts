import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { initTheme } from "@earendil-works/pi-coding-agent";
import recallExtension from "./index.ts";

const fixture = `<!-- recall-context: 1 -->
# Test investigation

Scope: Synthetic investigation.
Status: active

## Summary
The cause is unknown.

## Current understanding
Reported evidence does not establish a fix.

## Open questions and next actions
Ask for workload attribution.

## Details
Read-only savepoints do not imply ID-producing writes.

## Evidence
[Evidence](https://example.test/support)
`;

function toolWithBackend(generated = fixture) {
  let tool: any;
  const events: string[] = [];
  recallExtension({
    registerCommand() {},
    registerTool(candidate: any) { if (candidate.name === "recall_context") tool = candidate; },
    events: { emit(name: string) { events.push(name); } },
    async exec(binary: string, args: string[], options: any) {
      if (args[1] === "context" && args[2] === "validate") {
        const result = spawnSync(binary, args, { cwd: options.cwd, encoding: "utf8", timeout: 10_000 });
        return { code: result.status ?? 1, stdout: result.stdout ?? "", stderr: result.stderr ?? "" };
      }
      assert.equal(args[2], "create");
      return { code: 0, stdout: JSON.stringify({ draft: generated }), stderr: "" };
    },
  } as any);
  return { tool, events };
}

async function testPaths(t: any, label: string) {
  const name = `test-structured-${label}-${process.pid}-${Date.now()}`;
  const path = join(homedir(), ".recall", "contexts", `${name}.md`);
  const backup = join(homedir(), ".recall", "context-history", `${name}.md`);
  await mkdir(join(homedir(), ".recall", "contexts"), { recursive: true });
  t.after(async () => { await rm(path, { force: true }); await rm(backup, { force: true }); });
  return { name, path, backup };
}

const theme = {
  fg(_color: string, text: string) { return text; },
  bg(_color: string, text: string) { return text; },
  bold(text: string) { return text; },
};

// Exercise real Python validation and filesystem saves; only model output is synthetic.
test("Pi creation validates the document after full-editor changes and saves only after review", async (t) => {
  initTheme(undefined, false);
  const { name, path } = await testPaths(t, "create");
  const { tool } = toolWithBackend();
  let screen = 0;
  let loader: any;
  t.after(() => loader?.dispose?.());
  const edited = fixture.replace("The cause is unknown.", "AWS reported a problem; attribution remains unknown.");
  const result = await tool.execute("call", { action: "create", name, session_ids: ["fixture-session"], instruction: "Synthetic investigation" }, undefined, undefined, {
    hasUI: true, mode: "tui", cwd: tmpdir(), model: { provider: "test", id: "model" },
    ui: {
      async confirm() { return true; },
      async editor() { return edited; },
      async custom(factory: any) {
        screen++;
        if (screen === 1) return new Promise((resolve) => { loader = factory({ requestRender() {} }, theme, {}, resolve); });
        return screen === 2 ? "editor" : "apply";
      },
      notify() {},
    },
  });
  assert.equal(result.details.status, "created");
  assert.equal(await readFile(path, "utf8"), edited);
  assert.equal(screen, 3);
});

test("an unstructured generated draft is rejected before review or save", async (t) => {
  initTheme(undefined, false);
  const { name, path } = await testPaths(t, "invalid");
  const { tool } = toolWithBackend("# Not structured\n\nMissing sections.\n");
  let loader: any;
  t.after(() => loader?.dispose?.());
  await assert.rejects(tool.execute("call", { action: "create", name, session_ids: ["fixture-session"], instruction: "Synthetic investigation" }, undefined, undefined, {
    hasUI: true, mode: "tui", cwd: tmpdir(),
    ui: {
      async confirm() { return true; },
      async custom(factory: any) { return new Promise((resolve) => { loader = factory({ requestRender() {} }, theme, {}, resolve); }); },
      notify() {},
    },
  }), /format marker/);
  await assert.rejects(readFile(path), { code: "ENOENT" });
});

for (const scenario of ["valid", "invalid", "legacy", "concurrent"] as const) {
  test(`Pi update: ${scenario} proposal uses the shared save policy`, async (t) => {
    initTheme(undefined, false);
    const { name, path, backup } = await testPaths(t, scenario);
    const original = scenario === "legacy" ? "# Legacy\n\nRetain original formatting.\n" : fixture;
    await writeFile(path, original);
    const updated = scenario === "invalid" ? fixture.replace("## Summary", "## Missing") : fixture.replace("The cause is unknown.", "The cause remains under investigation.").replace("Ask for workload attribution.", "Await workload attribution from AWS.");
    const { tool } = toolWithBackend();
    let screen = 0;
    const execute = tool.execute("call", { action: "update", name, instruction: "Update the investigation." }, undefined, undefined, {
      hasUI: true, mode: "tui", cwd: tmpdir(),
      ui: {
        async custom(factory: any) {
          screen++;
          // Isolate provider generation; exercise review, validation, concurrency check and save.
          if (screen === 1) return { updated, edits: [], diff: "@@ summary and next action @@", reviewNotes: [] };
          if (scenario === "valid") {
            const component = factory({ terminal: { rows: 40 }, requestRender() {} }, theme, {}, () => {});
            assert.match(component.render(120).join("\n"), /Full document/);
            component.handleInput("v");
            assert.match(component.render(120).join("\n"), /Scope: Synthetic investigation/);
          }
          if (scenario === "concurrent") await writeFile(path, original + "\nConcurrent edit.\n");
          return "apply";
        },
        notify() {},
      },
    });
    if (scenario === "valid") {
      const result = await execute;
      assert.equal(result.details.status, "updated");
      assert.equal(await readFile(path, "utf8"), updated);
      assert.equal(await readFile(backup, "utf8"), original);
    } else {
      await assert.rejects(execute, scenario === "invalid" ? /context sections/ : scenario === "legacy" ? /not upgraded in place/ : /changed while/);
      assert.equal(await readFile(path, "utf8"), scenario === "concurrent" ? original + "\nConcurrent edit.\n" : original);
      await assert.rejects(readFile(backup), { code: "ENOENT" });
    }
  });
}

test("Pi update re-reviews editor-introduced scope changes and warnings before saving", async (t) => {
  initTheme(undefined, false);
  const { name, path } = await testPaths(t, "editor");
  await writeFile(path, fixture);
  const { tool } = toolWithBackend();
  const edited = fixture.replace("Scope: Synthetic investigation.", "Scope: Expanded synthetic investigation.").replace("Ask for workload attribution.", "Await vendor attribution.");
  let screen = 0;
  const result = await tool.execute("call", { action: "update", name, instruction: "Update the investigation." }, undefined, undefined, {
    hasUI: true, mode: "tui", cwd: tmpdir(),
    ui: {
      async custom(factory: any) {
        screen++;
        if (screen === 1) return { updated: fixture, edits: [], diff: "Initial proposal", reviewNotes: [] };
        if (screen === 2) return "editor";
        assert.equal(screen, 3, "Do not regenerate after a full-editor change");
        const component = factory({ terminal: { rows: 60 }, requestRender() {} }, theme, {}, () => {});
        const rendered = component.render(160).join("\n");
        assert.match(rendered, /Scope changed/);
        assert.match(rendered, /Summary did not/);
        assert.match(rendered, /Format v1/);
        return "apply";
      },
      async editor() { return edited; },
      async confirm() { throw new Error("Do not save through a generic confirmation that hides validation warnings"); },
      notify() {},
    },
  });
  assert.equal(result.details.status, "updated");
  assert.match(await readFile(path, "utf8"), /Expanded synthetic investigation/);
  assert.equal(screen, 3);
});

test("showing a legacy context leaves its exact bytes unchanged", async (t) => {
  const { name, path } = await testPaths(t, "read");
  const legacy = "# Old handoff\n\nNo schema, no automatic restructuring.\n";
  await writeFile(path, legacy);
  const { tool } = toolWithBackend();
  const result = await tool.execute("call", { action: "show", name }, undefined, undefined, {});
  assert.equal(result.content[0].text, legacy);
  assert.equal(await readFile(path, "utf8"), legacy);
});
