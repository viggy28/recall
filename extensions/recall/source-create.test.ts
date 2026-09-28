import assert from "node:assert/strict";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import { initTheme } from "@earendil-works/pi-coding-agent";
import recallExtension from "./index.ts";

function registeredContextTool(onExec?: (options: any) => void): any {
  let contextTool: any;
  const pi = {
    registerCommand() {},
    async exec(_binary: string, args: string[], options: any) {
      onExec?.(options);
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
  assert.equal(contextTool.promptGuidelines.some((line: string) => line.includes("focus/theme") && line.includes("discovers indexed sessions locally")), true);
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

test("context generation shows cancellable progress after approval", async (t) => {
  initTheme(undefined, false);
  let contextTool: any;
  let customCalls = 0;
  let loaderType = "";
  let loaderComponent: any;
  t.after(() => loaderComponent?.dispose?.());
  let generationSignal: AbortSignal | undefined;
  let releaseGeneration!: () => void;
  const source = tmpdir();
  const generationGate = new Promise<void>((resolve) => { releaseGeneration = resolve; });
  const pi = {
    registerCommand() {},
    async exec(_binary: string, args: string[], options: any) {
      if (args.includes("source-info")) {
        return { code: 0, stdout: JSON.stringify({ path: source, disclosure: `Path: ${source}` }), stderr: "" };
      }
      if (args.includes("create")) {
        generationSignal = options.signal;
        await generationGate;
        return { code: 0, stdout: JSON.stringify({ draft: "# Progress test\n" }), stderr: "" };
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
