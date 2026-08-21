import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdtemp, mkdir, realpath, symlink, writeFile } from "node:fs/promises";
import { homedir, tmpdir } from "node:os";
import { join, resolve } from "node:path";
import test from "node:test";
import {
  buildSourceGenerationPrompt,
  collectSourceSnapshot,
  formatSourceEvidence,
  prepareSourceRoot,
  resolveSourcePath,
  SOURCE_PROMPT_CHAR_LIMIT,
  sourceSnapshotSummary,
} from "./source-collector.ts";

async function fixture(prefix = "recall-source-"): Promise<string> {
  return mkdtemp(join(tmpdir(), prefix));
}

async function put(root: string, path: string, content: string | Buffer): Promise<void> {
  await mkdir(join(root, path, ".."), { recursive: true });
  await writeFile(join(root, path), content);
}

test("resolves relative, absolute, and home paths lexically", () => {
  assert.equal(resolveSourcePath("repo", "/work"), resolve("/work/repo"));
  assert.equal(resolveSourcePath("/tmp/repo", "/work"), resolve("/tmp/repo"));
  assert.equal(resolveSourcePath("~/repo", "/work"), join(homedir(), "repo"));
});

test("prefers tracked Git files and ignores untracked files", async () => {
  const root = await fixture();
  await put(root, "README.md", "tracked readme");
  await put(root, "src/main.ts", "export const value = 1;\n");
  execFileSync("git", ["init", "-q", root]);
  execFileSync("git", ["-C", root, "add", "README.md", "src/main.ts"]);
  await put(root, "untracked.txt", "do not include");
  const snapshot = await collectSourceSnapshot(root);
  assert.equal(snapshot.mode, "git");
  assert.deepEqual(snapshot.listing, ["README.md", "src/main.ts"]);
  assert.equal(snapshot.files.some((file) => file.path === "untracked.txt"), false);
});

test("falls back to deterministic non-Git traversal and prioritizes repository guidance", async () => {
  const root = await fixture();
  await put(root, "z.txt", "z");
  await put(root, "src/app.ts", "app");
  await put(root, "README.md", "readme");
  await put(root, "CLAUDE.md", "guidance");
  const first = await collectSourceSnapshot(root, { limits: { maxFiles: 3 } });
  const second = await collectSourceSnapshot(root, { limits: { maxFiles: 3 } });
  assert.equal(first.mode, "filesystem");
  assert.deepEqual(first.files.map((file) => file.path), ["CLAUDE.md", "README.md", "src/app.ts"]);
  assert.deepEqual(first, second);
});

test("excludes secrets, dependencies, generated files, binary content, and secret-looking content", async () => {
  const root = await fixture();
  await put(root, ".env", "SECRET=not-shown");
  await put(root, ".envrc", "export TOKEN=not-shown");
  await put(root, "node_modules/pkg/index.js", "dependency");
  await put(root, "dist/out.js", "generated");
  await put(root, "image.dat", Buffer.from([0, 1, 2, 3]));
  await put(root, "config.txt", "api_key = abcdefghijklmnopqrstuvwxyz123456");
  await put(root, "settings.txt", "OPENAI_API_KEY = sk_test_abcdefghijklmnopqrstuvwxyz");
  await put(root, "safe.txt", "safe content");
  const snapshot = await collectSourceSnapshot(root);
  assert.deepEqual(snapshot.files.map((file) => file.path), ["safe.txt"]);
  assert.equal(snapshot.skipped.some((item) => item.path === ".env" && item.reason === "excluded"), true);
  assert.equal(snapshot.skipped.some((item) => item.path === ".envrc" && item.reason === "excluded"), true);
  assert.equal(snapshot.skipped.some((item) => item.path === "image.dat" && item.reason === "binary"), true);
  assert.equal(snapshot.skipped.some((item) => item.path === "config.txt" && item.reason === "secret-content"), true);
  assert.equal(snapshot.skipped.some((item) => item.path === "settings.txt" && item.reason === "secret-content"), true);
  const evidence = formatSourceEvidence(snapshot);
  assert.equal(evidence.includes("abcdefghijklmnopqrstuvwxyz123456"), false);
  assert.equal(evidence.includes("sk_test_abcdefghijklmnopqrstuvwxyz"), false);
});

test("skips symlinks and rejects Git paths outside the canonical root", async (t) => {
  const root = await fixture();
  const outside = await fixture("recall-outside-");
  await put(outside, "outside.txt", "outside secret-free text");
  try {
    await symlink(join(outside, "outside.txt"), join(root, "linked.txt"));
  } catch (error: any) {
    if (error?.code === "EPERM") return t.skip("symlinks unavailable");
    throw error;
  }
  await put(root, "inside.txt", "inside");
  const snapshot = await collectSourceSnapshot(root, { gitFiles: async () => ["inside.txt", "linked.txt", "../outside.txt"] });
  assert.deepEqual(snapshot.files.map((file) => file.path), ["inside.txt"]);
  assert.equal(snapshot.skipped.some((item) => item.path === "linked.txt" && item.reason === "symlink"), true);
  assert.equal(snapshot.files.some((file) => file.content.includes("outside")), false);
  assert.equal(snapshot.skipped.some((item) => item.path === "../outside.txt" && item.reason === "outside-root"), true);
});

test("enforces per-file, total, and file-count limits with truncation metadata", async () => {
  const root = await fixture();
  await put(root, "README.md", "1234567890");
  await put(root, "a.txt", "abcdefghij");
  await put(root, "b.txt", "ABCDEFGHIJ");
  const snapshot = await collectSourceSnapshot(root, { limits: { maxFiles: 2, maxFileBytes: 6, maxTotalBytes: 9 } });
  assert.equal(snapshot.files.length, 2);
  assert.equal(snapshot.bytesRead, 9);
  assert.equal(snapshot.files[0]?.truncated, true);
  assert.equal(snapshot.files[1]?.truncated, true);
  assert.equal(snapshot.skipped.some((item) => item.reason === "file-limit"), true);
  const summary = sourceSnapshotSummary(snapshot).join("\n");
  assert.match(summary, /2 files · 9 selected bytes \(9 inspected\)/);
  assert.match(summary, /2 truncated/);
});

test("checks cancellation before collection and during Git discovery", async () => {
  const root = await fixture();
  const already = new AbortController();
  already.abort();
  await assert.rejects(collectSourceSnapshot(root, { signal: already.signal }), { name: "AbortError" });

  const during = new AbortController();
  await assert.rejects(collectSourceSnapshot(root, {
    signal: during.signal,
    gitFiles: async (_root, signal) => {
      during.abort();
      if (signal?.aborted) throw Object.assign(new Error("aborted"), { name: "AbortError" });
      return [];
    },
  }), { name: "AbortError" });
});

test("bounds directory traversal and still prioritizes root guidance", async () => {
  const root = await fixture();
  await put(root, "a/1.txt", "one");
  await put(root, "a/2.txt", "two");
  await put(root, "README.md", "important");
  const snapshot = await collectSourceSnapshot(root, { limits: { maxCandidates: 2, maxDirectories: 2 } });
  assert.equal(snapshot.candidateLimitReached, true);
  assert.equal(snapshot.files.some((file) => file.path === "README.md"), true);
});

test("fails closed when Git discovery has an operational error", async () => {
  const root = await fixture();
  await put(root, "README.md", "important");
  await assert.rejects(collectSourceSnapshot(root, {
    gitFiles: async () => { throw new Error("corrupt Git index"); },
  }), /corrupt Git index/);
});

test("caps serialized evidence even for escape-heavy text", async () => {
  const root = await fixture();
  await put(root, "README.md", `# Project\n${"\\\"\n".repeat(20_000)}`);
  const snapshot = await collectSourceSnapshot(root, { limits: { maxEvidenceChars: 12_000, maxTotalBytes: 24_000, maxFileBytes: 24_000 } });
  const evidence = formatSourceEvidence(snapshot);
  assert.ok(evidence.length <= 12_000);
  assert.equal(JSON.parse(evidence).files[0].truncated, true);
});

test("caps the complete generation prompt including oversized instructions", async () => {
  const root = await fixture();
  await put(root, "README.md", `# Project\n${"content\n".repeat(15_000)}`);
  const snapshot = await collectSourceSnapshot(root, { limits: { maxEvidenceChars: 119_999, maxTotalBytes: 80_000, maxFileBytes: 80_000 } });
  const prompt = buildSourceGenerationPrompt("project", "o".repeat(160_000), "r".repeat(160_000), snapshot);
  assert.ok(prompt.length <= SOURCE_PROMPT_CHAR_LIMIT);
  assert.match(prompt, /Instruction truncated/);
});

test("quotes control characters in review path summaries", async () => {
  const root = await fixture();
  await put(root, "line\nbreak.txt", "safe");
  const snapshot = await collectSourceSnapshot(root);
  const summary = sourceSnapshotSummary(snapshot).join("\n");
  assert.equal(summary.includes("line\nbreak.txt"), false);
  assert.match(summary, /line\\nbreak\.txt/);
});

test("validates collection limits", async () => {
  const root = await fixture();
  await assert.rejects(collectSourceSnapshot(root, { limits: { maxFiles: 0 } }), /positive integer/);
});

test("reports missing paths and non-directories", async () => {
  const root = await fixture();
  await assert.rejects(prepareSourceRoot(join(root, "missing")), /ENOENT/);
  await put(root, "file.txt", "text");
  await assert.rejects(collectSourceSnapshot(join(root, "file.txt")), /not a directory/);
});

test("canonicalizes a symlinked root", async (t) => {
  const target = await fixture();
  const parent = await fixture();
  const link = join(parent, "repo-link");
  try {
    await symlink(target, link, "dir");
  } catch (error: any) {
    if (error?.code === "EPERM") return t.skip("symlinks unavailable");
    throw error;
  }
  const prepared = await prepareSourceRoot(link);
  assert.equal(prepared.lexicalRoot, resolve(link));
  assert.equal(prepared.canonicalRoot, await realpath(target));
});

test("formats structured untrusted evidence with relative references", async () => {
  const root = await fixture();
  await put(root, "README.md", "# Project");
  const snapshot = await collectSourceSnapshot(root);
  const evidence = JSON.parse(formatSourceEvidence(snapshot));
  assert.match(evidence.notice, /UNTRUSTED/);
  assert.equal(evidence.collection.root_label, ".");
  assert.equal(evidence.files[0].relative_path, "README.md");
  assert.equal(evidence.files[0].content, "# Project");
  const prompt = buildSourceGenerationPrompt("project", "create from source", "focus on constraints", snapshot);
  assert.match(prompt, /<original_user_instruction>\ncreate from source/);
  assert.match(prompt, /<review_revision>\nfocus on constraints/);
  assert.match(prompt, /repository-relative file paths/);
  assert.match(prompt, /"relative_path":"README.md"/);
});
