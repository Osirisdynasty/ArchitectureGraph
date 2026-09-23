"""Run named P1/P2/P3 mutation probes without changing the working tree."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT = Path(__file__).resolve().parents[1]
PROBES = {
    "P1_class_route_method_source": (
        "test_mvp.MVPTests.test_p1_class_route_decorator_exposes_its_method_without_consuming",
        "route_functions.extend(member for member in n.body if isinstance(member,(ast.FunctionDef,ast.AsyncFunctionDef)))",
        "route_functions.extend(())",
    ),
    "P2_python_client_consumption": (
        "test_mvp.MVPTests.test_p2_python_http_client_call_still_consumes",
        "if id(n) not in decorator_calls: self._api_call(src,n)",
        "if False: self._api_call(src,n)",
    ),
    "P3_js_router_alias_host": (
        "test_mvp.MVPTests.test_p3_js_api_and_router_alias_route_hosts_do_not_consume",
        "route_receivers.update(self._local_route_aliases())",
        "route_receivers.update(())",
    ),
}


def run_test(repo: Path, test: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "unittest", "-v", test],
        cwd=repo / "tests", env=env, text=True, capture_output=True,
    )


def failure_count(output: str) -> int:
    match = re.search(r"FAILED \(failures=(\d+)(?:, errors=(\d+))?\)", output)
    return sum(map(int, match.groups(default="0"))) if match else 0


def show(label: str, result: subprocess.CompletedProcess[str]) -> None:
    output = result.stdout + result.stderr
    print(f"{label}: EXIT={result.returncode} FAILURES={failure_count(output)}")
    print(output, end="" if output.endswith("\n") else "\n")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="archgraph-p1-p3-probes-") as directory:
        work = Path(directory) / "repo"
        shutil.copytree(PROJECT, work, ignore=shutil.ignore_patterns(".venv-codex", "__pycache__", "*.db"))
        for name, (test, before, after) in PROBES.items():
            source = work / "src" / "archgraph" / "indexer.py"
            pristine = source.read_text(encoding="utf-8")
            print(f"=== {name} ===")
            baseline = run_test(work, test)
            show("BEFORE", baseline)
            if baseline.returncode:
                raise SystemExit(f"{name}: baseline unexpectedly failed")
            if pristine.count(before) != 1:
                raise SystemExit(f"{name}: expected one mutation anchor, got {pristine.count(before)}")
            source.write_text(pristine.replace(before, after), encoding="utf-8")
            broken = run_test(work, test)
            show("AFTER", broken)
            if broken.returncode == 0 or failure_count(broken.stdout + broken.stderr) == 0:
                raise SystemExit(f"{name}: mutation did not make the suite red")
            source.write_text(pristine, encoding="utf-8")


if __name__ == "__main__":
    main()
