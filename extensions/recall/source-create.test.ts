import assert from "node:assert/strict";
import { execFileSync, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { mkdtemp, rm } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { tmpdir } from "node:os";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { initTheme } from "@earendil-works/pi-coding-agent";
import recallExtension from "./index.ts";

function structuredDraft(summary = "Verified findings."): string {
  return `<!-- recall-context: 2 -->\n# Reader restart\n\n## Summary\n${summary}\n\n## Open questions and next actions\nAsk for workload attribution.\n\n---\n\nSupporting detail follows.\n\n## Current understanding\nReported findings, not a confirmed fix. [E1]\n\n## Evidence\n- **E1** — [Source](https://example.test/evidence)\n\n### Context metadata\n- Scope: Reader restart investigation.\n- Status: active\n`;
}

function realBackend(binary: string, args: string[], options: any) {
  const result = spawnSync(binary, args, { cwd: options.cwd, encoding: "utf8", timeout: 10_000 });
  return { code: result.status ?? 1, stdout: result.stdout ?? "", stderr: result.stderr ?? String(result.error ?? "") };
}

function registeredContextTool(
  onExec?: (options: any) => void,
  backendExec?: (binary: string, args: string[], options: any) => Promise<{ code: number; stdout: string; stderr: string }>,
): any {
  let contextTool: any;
  const pi = {
    on() {},
    registerCommand() {},
    events: { emit() {} },
    async exec(binary: string, args: string[], options: any) {
      onExec?.(options);
      if (args[1] === "context" && args[2] === "validate") return realBackend(binary, args, options);
      if (backendExec) return backendExec(binary, args, options);
      const index = args.indexOf("source-info");
      const path = index >= 0 ? args[index + 1] : "";
      return { code: 0, stdout: JSON.stringify({ path, disclosure: `Path (absolute, not yet accessed): ${path}\nFixed limits: 40 files` }), stderr: "" };
    },
    registerTool(tool: any) {
      if (tool.name === "recall_context") contextTool = tool;
    },
  };
  recallExtension(pi as any);
  assert.ok(contextTool);
  return contextTool;
}

test("registers source-first creation guidance and removes save_current", () => {
  const contextTool = registeredContextTool();
  assert.ok(contextTool.parameters.properties.source_path);
  assert.equal(contextTool.parameters.required.includes("source_path"), false);
  assert.ok(contextTool.parameters.properties.session_ids);
  assert.ok(contextTool.parameters.properties.current_session);
  assert.equal(contextTool.promptGuidelines.some((line: string) => line.includes("focus/theme") && line.includes("Discover indexed sessions only")), true);
  assert.equal(contextTool.promptGuidelines.some((line: string) => line.includes("session_ids") && line.includes("current_session: true")), true);
  assert.equal(contextTool.parameters.properties.instruction.description.includes("never evidence"), true);
  assert.equal(JSON.stringify(contextTool.parameters.properties.action).includes("save_current"), false);
});

test("rejects blank source_path and source_path on non-create actions", async () => {
  const contextTool = registeredContextTool();
  await assert.rejects(contextTool.execute("call", { action: "create", name: "blank-source", instruction: "create", source_path: "   " }, undefined, undefined, {}), /must not be empty/);
  await assert.rejects(contextTool.execute("call", { action: "show", name: "recall", source_path: "/tmp/repo" }, undefined, undefined, {}), /only for create/);
});

test("non-TUI creation fails loudly without model or filesystem work", async () => {
  const contextTool = registeredContextTool();
  let confirms = 0;
  const result = await contextTool.execute(
    "call",
    { action: "create", name: `ordinary-${process.pid}-${Date.now()}`, instruction: "Capture ordinary durable project facts." },
    new AbortController().signal,
    () => {},
    {
      hasUI: true,
      mode: "rpc",
      cwd: tmpdir(),
      model: { provider: "test-provider", id: "test-model" },
      ui: {
        async custom() { return null; },
        async confirm() { confirms++; return false; },
        async editor() { throw new Error("editor must not be called"); },
        notify() {},
      },
      sessionManager: { getBranch() { return []; } },
      modelRegistry: {},
    },
  );
  assert.equal(confirms, 0);
  assert.equal(result.details.status, "failed");
  assert.match(result.content[0].text, /failed/i);
  assert.match(result.details.error, /TUI mode/);
});

test("an incompatible discovery backend identifies the mixed installation before any approval", async () => {
  let backendPath = "";
  let python = "";
  let calls = 0;
  const name = `core-db-reader-restart-${process.pid}-${Date.now()}`;
  const tool = registeredContextTool(undefined, async (binary, args) => {
    calls++;
    python = binary;
    backendPath = args[0]!;
    assert.deepEqual(args.slice(1), ["context", "discover", name.replaceAll("-", " "), "--offset", "0", "--limit", "5"]);
    return {
      code: 2,
      stdout: "",
      stderr: "usage: recall [-h] {index,search,recent,graph,go,tui,context} ...\nrecall: error: unrecognized arguments: core db reader restart --offset 0 --limit 5\n",
    };
  });
  await assert.rejects(
    tool.execute("call", { action: "create", name }, undefined, undefined, {
      hasUI: true,
      mode: "tui",
      cwd: tmpdir(),
      ui: {
        async custom() { throw new Error("No approval UI should open before discovery succeeds"); },
        async confirm() { throw new Error("No approval should be requested"); },
      },
    }),
    (error: Error) => {
      assert.match(error.message, /incompatible/i);
      assert.ok(error.message.includes(backendPath));
      assert.ok(error.message.includes(python));
      assert.match(error.message, /whole Recall package/);
      assert.match(error.message, /\/reload/);
      assert.match(error.message, /No context was saved/);
      assert.match(error.message, /unrecognized arguments/);
      return true;
    },
  );
  assert.equal(calls, 1, "Do not fall back to another CLI or write a context directly");
});

test("session creation runs real backend discovery and reaches transmission approval", async (t) => {
  const home = await mkdtemp(join(tmpdir(), "recall-discovery-"));
  t.after(() => rm(home, { recursive: true, force: true }));
  const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
  const venvPython = join(root, ".venv", "bin", "python");
  const python = process.env.RECALL_PYTHON || (existsSync(venvPython) ? venvPython : "python3");
  const session = "fixture-reader-restart";
  execFileSync(python, ["-c", `
import sys
sys.path.insert(0, sys.argv[1])
import recall
conn = recall.connect()
recall.init_db(conn)
conn.execute("""INSERT INTO messages
(path, session_id, source, project, role, type, ts, epoch, line_no, text, nl_text)
VALUES (?, ?, 'pi', '/fixture', 'user', 'user', '2026-10-09T00:00:00Z', 1791504000, 1, ?, ?)""",
('/fixture/session.jsonl', sys.argv[2], 'Core DB reader restart', 'Core DB reader restart'))
conn.commit()
`, root, session], { env: { ...process.env, HOME: home }, timeout: 10_000 });

  let backendCalls = 0;
  const tool = registeredContextTool(undefined, async (binary, args, options) => {
    backendCalls++;
    const stdout = execFileSync(binary, args, {
      cwd: options.cwd,
      env: { ...process.env, HOME: home },
      encoding: "utf8",
      timeout: 10_000,
    });
    const candidates = JSON.parse(stdout);
    assert.equal(candidates[0].session_id, session);
    return { code: 0, stdout, stderr: "" };
  });
  let selections = 0;
  let approvals = 0;
  const result = await tool.execute(
    "call",
    { action: "create", name: `core-db-reader-restart-${process.pid}-${Date.now()}`, instruction: "Reader restart findings and unresolved questions" },
    undefined,
    undefined,
    {
      hasUI: true,
      mode: "tui",
      cwd: home,
      model: { provider: "test-provider", id: "test-model" },
      ui: {
        async custom() { selections++; return "0"; },
        async confirm(title: string, detail: string) {
          approvals++;
          assert.equal(title, "Send selected transcript evidence?");
          assert.ok(detail.includes(session));
          assert.match(detail, /test-provider\/test-model/);
          assert.match(detail, /Saving requires a separate reviewed action/);
          return false;
        },
        async editor() { throw new Error("Focus was supplied"); },
      },
    },
  );
  assert.equal(backendCalls, 1);
  assert.equal(selections, 2, "Select the matching session, then confirm the selection");
  assert.equal(approvals, 1);
  assert.equal(result.details.status, "cancelled");
  assert.match(result.content[0].text, /no file was written/);
});

test("explicit session sources skip discovery but retain evidence approval and draft review", async (t) => {
  initTheme(undefined, false);
  const requested = "01a1226f-1742-73aa-b55f-1ab14ac2565b";
  const active = "fixture-active-session";
  const scenarios = [
    { label: "explicit ID", parameters: { session_ids: [requested] }, expected: [requested] },
    { label: "this session", parameters: { current_session: true }, expected: [active] },
    { label: "multiple IDs", parameters: { session_ids: [requested, active] }, expected: [requested, active] },
    { label: "unavailable review", parameters: { session_ids: [requested] }, expected: [requested], reviewUnavailable: true },
  ];
  for (const scenario of scenarios) {
    await t.test(scenario.label, async (t) => {
      let calls = 0;
      const tool = registeredContextTool(undefined, async (_binary, args) => {
        calls++;
        assert.deepEqual(args.slice(1, 3), ["context", "create"], "Do not discover alternative sessions");
        const sessions = args.flatMap((arg, i) => arg === "--session" ? [args[i + 1]] : []);
        assert.deepEqual(sessions, scenario.expected);
        assert.ok(args.includes("--draft-only"));
        assert.ok(args.includes("--json"));
        return { code: 0, stdout: JSON.stringify({ draft: structuredDraft(), editorial_notes: [] }), stderr: "" };
      });
      let screens = 0;
      let approvals = 0;
      let loader: any;
      t.after(() => loader?.dispose?.());
      const result = await tool.execute("call", {
        action: "create",
        name: `reader-restart-${process.pid}-${Date.now()}`,
        instruction: "Reader restart findings and open questions",
        ...scenario.parameters,
      }, undefined, undefined, {
        hasUI: true,
        mode: "tui",
        cwd: tmpdir(),
        model: { provider: "test-provider", id: "test-model" },
        sessionManager: { getSessionId() { return active; } },
        ui: {
          async confirm(title: string, detail: string) {
            approvals++;
            assert.equal(title, "Send selected transcript evidence?");
            for (const id of scenario.expected) assert.ok(detail.includes(id));
            return true;
          },
          async custom(factory: any) {
            screens++;
            if (screens === 2) return "reviewUnavailable" in scenario ? undefined : "cancel"; // No Apply action: never save.
            assert.equal(screens, 1);
            return new Promise((resolve) => {
              loader = factory({ requestRender() {} }, { fg(_color: string, value: string) { return value; } }, {}, resolve);
            });
          },
          async editor() { throw new Error("A focus was supplied"); },
          notify() {},
        },
      });
      assert.equal(calls, 1);
      assert.equal(approvals, 1);
      assert.equal(screens, 2, "Generation progress, then draft review; no source-selection screen");
      assert.equal(result.details.status, "cancelled");
      assert.match(result.content[0].text, /no file was written/);
    });
  }
});

test("denying explicit-session evidence approval does not call the backend", async () => {
  const session = "01a1226f-1742-73aa-b55f-1ab14ac2565b";
  const tool = registeredContextTool(undefined, async () => { throw new Error("Do not read or generate evidence before approval"); });
  const result = await tool.execute("call", {
    action: "create", name: `reader-deny-${process.pid}-${Date.now()}`, session_ids: [session], instruction: "Reader restart findings",
  }, undefined, undefined, {
    hasUI: true, mode: "tui", cwd: tmpdir(),
    ui: {
      async confirm(_title: string, detail: string) { assert.ok(detail.includes(session)); return false; },
      async custom() { throw new Error("Do not discover or generate"); },
    },
  });
  assert.equal(result.details.status, "cancelled");
});

test("rejects conflicting or invalid session sources before opening the UI", async () => {
  const tool = registeredContextTool(undefined, async () => { throw new Error("No backend work expected"); });
  const invalid = [
    { session_ids: [] },
    { session_ids: [" "] },
    { session_ids: ["abc"] },
    { session_ids: ["valid-session"], source_path: "/tmp/repo" },
    { session_ids: ["valid-session"], current_session: true },
    { current_session: true, source_path: "/tmp/repo" },
  ];
  for (const params of invalid) {
    await assert.rejects(tool.execute("call", { action: "create", name: "reader-restart", ...params }, undefined, undefined, {}));
  }
  for (const params of [{ session_ids: ["valid-session"] }, { current_session: true }]) {
    await assert.rejects(tool.execute("call", { action: "show", name: "reader-restart", ...params }, undefined, undefined, {}), /only for create/);
  }
});

test("context generation shows cancellable progress after approval", async (t) => {
  initTheme(undefined, false);
  let contextTool: any;
  let customCalls = 0;
  let loaderType = "";
  let loaderComponent: any;
  t.after(() => loaderComponent?.dispose?.());
  let generationSignal: AbortSignal | undefined;
  const attentionEvents: Array<{ name: string; data: any }> = [];
  let releaseGeneration!: () => void;
  const source = tmpdir();
  const generationGate = new Promise<void>((resolve) => { releaseGeneration = resolve; });
  const pi = {
    on() {},
    registerCommand() {},
    events: {
      emit(name: string, data: any) { attentionEvents.push({ name, data }); },
    },
    async exec(binary: string, args: string[], options: any) {
      if (args[1] === "context" && args[2] === "validate") return realBackend(binary, args, options);
      if (args.includes("source-info")) {
        return { code: 0, stdout: JSON.stringify({ path: source, disclosure: `Path: ${source}` }), stderr: "" };
      }
      if (args.includes("create")) {
        generationSignal = options.signal;
        await generationGate;
        return { code: 0, stdout: JSON.stringify({ draft: structuredDraft("Progress test."), editorial_notes: [] }), stderr: "" };
      }
      throw new Error(`unexpected backend call: ${args.join(" ")}`);
    },
    registerTool(tool: any) {
      if (tool.name === "recall_context") contextTool = tool;
    },
  };
  recallExtension(pi as any);

  const execution = contextTool.execute(
    "call",
    { action: "create", name: `progress-${process.pid}-${Date.now()}`, instruction: "Durable architecture", source_path: source },
    new AbortController().signal,
    () => {},
    {
      hasUI: true,
      mode: "tui",
      cwd: source,
      model: { provider: "test-provider", id: "test-model" },
      ui: {
        async confirm() { return true; },
        async editor() { throw new Error("editor must not be called"); },
        async custom(factory: any) {
          customCalls++;
          if (customCalls > 1) return "cancel";
          return new Promise((resolve) => {
            let component: any;
            component = factory(
              { requestRender() {} },
              { fg(_color: string, value: string) { return value; } },
              {},
              (value: unknown) => {
                component?.dispose?.();
                resolve(value);
              },
            );
            loaderComponent = component;
            loaderType = component.constructor.name;
          });
        },
        notify() {},
      },
      sessionManager: { getBranch() { return []; } },
      modelRegistry: {},
    },
  );

  while (!generationSignal) await new Promise((resolve) => setImmediate(resolve));
  releaseGeneration();
  const result = await execution;

  assert.equal(loaderType, "BorderedLoader");
  assert.ok(generationSignal instanceof AbortSignal);
  assert.equal(result.details.status, "cancelled");
  assert.equal(customCalls, 2);
  assert.deepEqual(attentionEvents.map((event) => event.name), [
    "pi:attention-required",
    "pi:attention-resolved",
  ]);
  assert.match(attentionEvents[0].data.body, /Create .* is ready for review/);
  assert.equal(attentionEvents[0].data.id, attentionEvents[1].data.id);
});

test("denying source approval does not access the path or generate", async () => {
  const execOptions: any[] = [];
  const contextTool = registeredContextTool((options) => execOptions.push(options));
  let confirmations = 0;
  let modelRegistryCalls = 0;
  const missing = join(tmpdir(), `recall-must-not-access-${process.pid}-${Date.now()}`);
  const ctx = {
    hasUI: true,
    mode: "tui",
    cwd: tmpdir(),
    model: { provider: "test-provider", id: "test-model" },
    ui: {
      async confirm(title: string, detail: string) {
        confirmations++;
        assert.equal(title, "Inspect source directory?");
        assert.equal(detail.includes(missing), true);
        assert.match(detail, /test-provider\/test-model/);
        assert.match(detail, /bounded listing, omission metadata, and selected excerpts/);
        return false;
      },
      async editor() { throw new Error("editor must not be called"); },
      notify() { throw new Error("notify must not be called"); },
    },
    modelRegistry: {
      async getApiKeyAndHeaders() {
        modelRegistryCalls++;
        throw new Error("generation must not be called");
      },
    },
  };
  const result = await contextTool.execute(
    "call",
    { action: "create", name: "denied-source-test", instruction: "Create from supplied source.", source_path: missing },
    new AbortController().signal,
    () => {},
    ctx,
  );
  assert.equal(confirmations, 1);
  assert.equal(modelRegistryCalls, 0);
  assert.equal(execOptions.every((options) => options.cwd === tmpdir()), true);
  assert.equal(result.details.status, "cancelled");
  assert.match(result.content[0].text, /no file was written/);
});

test("update surfaces proposal failure instead of cancelled", async () => {
  const { mkdtemp, mkdir, writeFile, readFile } = await import("node:fs/promises");
  const { homedir } = await import("node:os");
  const { join } = await import("node:path");
  const dir = await mkdtemp(join(tmpdir(), "recall-update-fail-"));
  // Point contexts dir via writing into real home contexts with unique name
  const name = `upd-fail-${process.pid}-${Date.now()}`;
  const contextsDir = join(homedir(), ".recall", "contexts");
  await mkdir(contextsDir, { recursive: true });
  const path = join(contextsDir, `${name}.md`);
  await writeFile(path, `# ${name}\n## Current state\n- old fact\n`, "utf8");
  try {
    const contextTool = registeredContextTool();
    const notifies: string[] = [];
    const result = await contextTool.execute(
      "call",
      { action: "update", name, instruction: "Add a new fact about progress." },
      new AbortController().signal,
      () => {},
      {
        hasUI: true,
        mode: "tui",
        cwd: dir,
        model: { provider: "test-provider", id: "test-model", api: "openai-completions" },
        ui: {
          async custom() {
            // Simulate custom UI never binding (undefined), so generation falls through
            return undefined;
          },
          async confirm() { throw new Error("confirm should not run when proposal fails"); },
          async editor() { throw new Error("editor must not be called"); },
          notify(message: string) { notifies.push(message); },
        },
        sessionManager: { getBranch() { return []; } },
        modelRegistry: {
          async getApiKeyAndHeaders() {
            return { ok: false, error: "No API key found for \"test-provider\"" };
          },
        },
      },
    );
    assert.equal(result.details.status, "failed");
    assert.match(result.content[0].text, /Context update failed/);
    assert.match(result.content[0].text, /No API key/);
    assert.match(result.details.error, /No API key/);
    // File unchanged
    assert.match(await readFile(path, "utf8"), /old fact/);
  } finally {
    await import("node:fs/promises").then((fs) => fs.unlink(path).catch(() => undefined));
  }
});

test("update with broken provider reports failed, never silent cancelled", async () => {
  const { mkdir, writeFile, readFile, unlink } = await import("node:fs/promises");
  const { homedir } = await import("node:os");
  const { join } = await import("node:path");

  const name = `upd-rpc-${process.pid}-${Date.now()}`;
  const contextsDir = join(homedir(), ".recall", "contexts");
  await mkdir(contextsDir, { recursive: true });
  const path = join(contextsDir, `${name}.md`);
  const original = `# ${name}\n## Current state\n- old fact\n`;
  await writeFile(path, original, "utf8");

  const contextTool = registeredContextTool();
  try {
    const result = await contextTool.execute(
      "call",
      { action: "update", name, instruction: "Change old fact to new fact." },
      new AbortController().signal,
      () => {},
      {
        hasUI: true,
        mode: "rpc",
        cwd: tmpdir(),
        model: { provider: "test-provider", id: "test-model", api: "openai-completions" },
        ui: {
          async custom() { return undefined; },
          async confirm() { return true; },
          async editor() { throw new Error("editor must not be called"); },
          notify() {},
        },
        sessionManager: { getBranch() { return []; } },
        modelRegistry: {
          async getApiKeyAndHeaders() {
            return { ok: true, apiKey: "test-key", headers: {} };
          },
        },
      },
    );
    // Without a real provider endpoint, complete() fails — must be failed, never silent cancelled.
    assert.equal(result.details.status, "failed");
    assert.match(result.content[0].text, /Context update failed/);
    assert.equal(await readFile(path, "utf8"), original);
  } finally {
    await unlink(path).catch(() => undefined);
  }
});
