import assert from "node:assert/strict";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
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

test("non-TUI creation cancels without model or filesystem work", async () => {
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
  assert.equal(result.details.status, "cancelled");
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
