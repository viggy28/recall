import assert from "node:assert/strict";
import { join } from "node:path";
import { tmpdir } from "node:os";
import test from "node:test";
import recallExtension from "./index.ts";

function registeredContextTool(): any {
  let contextTool: any;
  const pi = {
    registerCommand() {},
    registerTool(tool: any) {
      if (tool.name === "recall_context") contextTool = tool;
    },
  };
  recallExtension(pi as any);
  assert.ok(contextTool);
  return contextTool;
}

test("registers the explicit source_path contract and exact natural prompt guidance", () => {
  const contextTool = registeredContextTool();
  assert.ok(contextTool.parameters.properties.source_path);
  assert.equal(contextTool.parameters.required.includes("source_path"), false);
  assert.equal(contextTool.promptGuidelines.some((line: string) => line.includes("create recall context for safe-notsafe from the source.") && line.includes("~/source/github/viggy28/safe-not-safe")), true);
});

test("rejects blank source_path and source_path on non-create actions", async () => {
  const contextTool = registeredContextTool();
  await assert.rejects(contextTool.execute("call", { action: "create", name: "blank-source", instruction: "create", source_path: "   " }, undefined, undefined, {}), /must not be empty/);
  await assert.rejects(contextTool.execute("call", { action: "show", name: "recall", source_path: "/tmp/repo" }, undefined, undefined, {}), /only for create/);
});

test("ordinary description create remains source-approval free", async () => {
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
  const contextTool = registeredContextTool();
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
        assert.match(detail, /file listing, omission metadata, and selected source excerpts .*will be sent/);
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
  assert.equal(result.details.status, "cancelled");
  assert.match(result.content[0].text, /no file was written/);
});
