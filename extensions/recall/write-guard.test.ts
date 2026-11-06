import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, symlink, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join, relative } from "node:path";
import { pathToFileURL } from "node:url";
import test from "node:test";
import recallExtension, { protectedRecallDestination } from "./index.ts";

async function paths(t: any) {
  const directory = await mkdtemp(join(tmpdir(), "recall-write-guard-test-"));
  t.after(() => rm(directory, { recursive: true, force: true }));
  const contexts = join(directory, "with space", "contexts");
  const history = join(directory, "history");
  await mkdir(contexts, { recursive: true });
  await mkdir(history);
  return { directory, contexts, history, roots: [contexts, history] };
}

test("write guard recognizes absolute, relative, normalized, prefixed, and file-URL destinations", async (t) => {
  const { directory, contexts, history, roots } = await paths(t);
  const file = join(contexts, "new.md");
  for (const path of [file, relative(directory, file), "@" + file, pathToFileURL(file).href,
    "@" + pathToFileURL(file).href, file.replace("with space", "with\u00a0space"),
    join(contexts, "child", "..", "new.md"), history]) {
    assert.equal(await protectedRecallDestination(path, directory, roots), true, path);
  }
  assert.equal(await protectedRecallDestination("~/.recall/contexts/new.md", directory), true);
  assert.equal(await protectedRecallDestination("~/.recall/context-history/new.md", directory), true);
});

test("write guard does not block neighboring directories or ordinary project files", async (t) => {
  const { directory, contexts, roots } = await paths(t);
  for (const path of [contexts + "-copy/file.md", join(directory, "project", "context.md")]) {
    assert.equal(await protectedRecallDestination(path, directory, roots), false);
  }
});

test("write guard follows directory aliases, existing file aliases, and dangling file symlinks", async (t) => {
  const { directory, contexts, roots } = await paths(t);
  const existing = join(contexts, "existing.md");
  await writeFile(existing, "unchanged sentinel");
  const alias = join(directory, "alias");
  const fileAlias = join(directory, "file-alias.md");
  const dangling = join(directory, "dangling.md");
  await symlink(contexts, alias, "dir");
  await symlink(existing, fileAlias, "file");
  await symlink(join(contexts, "not-created-yet.md"), dangling, "file");
  for (const path of [join(alias, "new.md"), fileAlias, dangling]) {
    assert.equal(await protectedRecallDestination(path, directory, roots), true, path);
  }
  assert.equal(await readFile(existing, "utf8"), "unchanged sentinel");
});

test("write guard protects relocated roots and missing roots reached through dangling directory links", async (t) => {
  const { directory, contexts } = await paths(t);
  const relocated = join(directory, "relocated");
  await symlink(contexts, relocated, "dir");
  assert.equal(await protectedRecallDestination(join(contexts, "new.md"), directory, [relocated]), true);
  const missing = join(directory, "missing", "contexts");
  const alias = join(directory, "dangling-root");
  await symlink(missing, alias, "dir");
  assert.equal(await protectedRecallDestination(join(alias, "new.md"), directory, [missing]), true);
});

test("registered hook denies direct write/edit but leaves reads, shell, and Recall operations alone", async () => {
  let hook: any;
  recallExtension({
    on(event: string, handler: any) { assert.equal(event, "tool_call"); hook = handler; },
    registerCommand() {}, registerTool() {},
  } as any);
  const ctx = { cwd: tmpdir() };
  const path = join(homedir(), ".recall", "contexts", "guard-sentinel.md");
  for (const toolName of ["write", "edit"]) {
    const result = await hook({ toolName, input: { path }, parentToolCallId: "nested-call" }, ctx);
    assert.equal(result.block, true);
    assert.match(result.reason, /recall_context/);
  }
  for (const toolName of ["read", "bash", "recall_context"]) {
    assert.equal(await hook({ toolName, input: { path } }, ctx), undefined);
  }
  assert.equal(await hook({ toolName: "write", input: { path: join(tmpdir(), "ordinary-project", "file.md") } }, ctx), undefined);
  assert.equal((await hook({ toolName: "edit", input: { path: "\0invalid" } }, ctx)).block, true);
});
