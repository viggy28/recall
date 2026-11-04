import assert from "node:assert/strict";
import { tmpdir } from "node:os";
import test from "node:test";
import recallExtension from "./index.ts";

test("search enum inputs are narrowed without changing valid CLI arguments", async () => {
  let tool: any;
  const calls: string[][] = [];
  recallExtension({
    registerCommand() {},
    registerTool(candidate: any) { if (candidate.name === "recall_search") tool = candidate; },
    async exec(_binary: string, args: string[]) {
      calls.push(args);
      return { code: 0, stdout: "[]", stderr: "" };
    },
  } as any);
  for (const mode of ["fuzzy", "regex", "semantic"]) {
    await tool.execute("call", { query: "context", mode, source: "pi" }, undefined, undefined, { cwd: tmpdir() });
    const args = calls.at(-1)!;
    assert.ok(args.includes("--source"));
    assert.ok(args.includes("pi"));
    assert.equal(args.includes("--regex"), mode === "regex");
    assert.equal(args.includes("--semantic"), mode === "semantic");
  }
  const before = calls.length;
  await assert.rejects(tool.execute("call", { query: "context", mode: {} }, undefined, undefined, {}), /Invalid Recall search mode/);
  await assert.rejects(tool.execute("call", { query: "context", source: {} }, undefined, undefined, {}), /Invalid Recall search source/);
  assert.equal(calls.length, before);
});
