"""Prove each S2 relative-import assertion turns red when its behavior is removed."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
TESTS = {
    "S2_A_same_directory": (
        "test_mvp.MVPTests.test_q2_c_imported_exported_router_alias_exposes_api",
        "if not source.startswith('.') or not bindings.startswith('{'):",
        "if True:",
    ),
    "S2_B_parent": (
        "test_mvp.MVPTests.test_s2_parent_relative_named_router_alias_exposes_api",
        "module=posixpath.normpath((self.path.parent / source).with_suffix('').as_posix())",
        "module=(self.path.parent / source).with_suffix('').as_posix()",
    ),
    "S2_C_grandparent": (
        "test_mvp.MVPTests.test_s2_grandparent_relative_named_router_alias_exposes_api",
        "module=posixpath.normpath((self.path.parent / source).with_suffix('').as_posix())",
        "module=(self.path.parent / source).with_suffix('').as_posix()",
    ),
    "S2_D_source_suffix": (
        "test_mvp.MVPTests.test_s2_parent_and_grandparent_source_suffix_named_router_aliases_expose_api",
        "module=posixpath.normpath((self.path.parent / source).with_suffix('').as_posix())",
        "module=(self.path.parent / source).with_suffix('').as_posix()",
    ),
}


def run(repo: Path, test: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "unittest", "-v", test],
        cwd=repo / "tests", env=env, text=True, capture_output=True,
    )


def show(label: str, result: subprocess.CompletedProcess[str]) -> None:
    print(f"{label}: EXIT={result.returncode}")
    output = result.stdout + result.stderr
    print(output, end="" if output.endswith("\n") else "\n")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="archgraph-s2-mutation-") as directory:
        work = Path(directory) / "repo"
        shutil.copytree(PROJECT, work, ignore=shutil.ignore_patterns(".venv-codex", "__pycache__", "*.db"))
        source = work / "src" / "archgraph" / "indexer.py"
        pristine = source.read_text(encoding="utf-8")
        for name, (test, before, after) in TESTS.items():
            print(f"=== {name}: {test} ===")
            baseline = run(work, test)
            show("BEFORE", baseline)
            if baseline.returncode:
                raise SystemExit(f"{name}: baseline unexpectedly failed")
            if pristine.count(before) != 1:
                raise SystemExit(f"{name}: expected one mutation anchor, got {pristine.count(before)}")
            source.write_text(pristine.replace(before, after), encoding="utf-8")
            broken = run(work, test)
            show("AFTER", broken)
            if broken.returncode == 0:
                raise SystemExit(f"{name}: mutation did not make its assertion red")
            source.write_text(pristine, encoding="utf-8")


if __name__ == "__main__":
    main()
