#!/usr/bin/env python3
"""Fail-safe change detection for the pull-request retrieval gate."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import PurePosixPath

SENSITIVE_PREFIXES = ("benchmarks/", "tests/", "scripts/ci/", ".github/workflows/")
SAFE_PREFIXES = ("docs/", ".github/ISSUE_TEMPLATE/")
SAFE_SUFFIXES = (".md", ".png", ".gif")


def valid_path(path: str) -> bool:
    pure = PurePosixPath(path)
    return bool(path) and not pure.is_absolute() and ".." not in pure.parts and "\\" not in path


def is_known_safe(path: str) -> bool:
    if not valid_path(path):
        return False
    if path.startswith(SENSITIVE_PREFIXES):
        return False
    return path.startswith(SAFE_PREFIXES) or path.lower().endswith(SAFE_SUFFIXES)


def decide(paths: list[str], *, force: bool = False,
           error: str | None = None) -> tuple[bool, str]:
    if force:
        return True, "forced by event or ci:retrieval label"
    if error:
        return True, f"change detection failed open: {error}"
    if not paths:
        return True, "empty or ambiguous diff; running fail-safe"
    unsafe = [path for path in paths if not is_known_safe(path)]
    if unsafe:
        return True, f"{len(unsafe)} retrieval-relevant or unknown path(s) changed"
    return False, f"all {len(paths)} changed path(s) are known-safe documentation/assets"


def changed_paths(base: str, head: str) -> list[str]:
    raw = subprocess.check_output(
        ["git", "diff", "--name-status", "-z", "--find-renames", base, head],
        stderr=subprocess.STDOUT,
    )
    fields = raw.decode("utf-8", errors="surrogateescape").split("\0")
    if fields and fields[-1] == "":
        fields.pop()
    paths: list[str] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        count = 2 if status.startswith(("R", "C")) else 1
        if index + count > len(fields):
            raise ValueError("malformed git name-status output")
        paths.extend(fields[index:index + count])
        index += count
    return paths


def _append(path: str | None, text: str) -> None:
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base")
    parser.add_argument("--head")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--label", action="append", default=[])
    parser.add_argument("--labels-json", default="[]")
    args = parser.parse_args(argv)

    try:
        json_labels = json.loads(args.labels_json)
        if not isinstance(json_labels, list):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        json_labels = []
    force = args.force or "ci:retrieval" in [*args.label, *json_labels]
    error = None
    paths: list[str] = []
    if not force:
        if not args.base or not args.head:
            error = "base/head SHA unavailable"
        else:
            try:
                paths = changed_paths(args.base, args.head)
            except (OSError, subprocess.CalledProcessError, ValueError) as exc:
                error = type(exc).__name__
    run, reason = decide(paths, force=force, error=error)
    value = "true" if run else "false"
    _append(os.environ.get("GITHUB_OUTPUT"), f"run={value}\nreason={reason}\n")
    _append(os.environ.get("GITHUB_STEP_SUMMARY"),
            f"## Retrieval scope\n\n- Decision: **{'run' if run else 'skip'}**\n"
            f"- Reason: {reason}\n- Changed path count: {len(paths)}\n")
    print(json.dumps({"run": run, "reason": reason, "changed_path_count": len(paths)},
                     sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
