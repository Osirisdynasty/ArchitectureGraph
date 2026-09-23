"""Run Q1/Q2/Q3 mutation probes in disposable copies of the complete suite."""
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
    "Q1_api_client_vs_router_host_precedence": (
        "CLIENT_RECEIVERS={'api','client','http','https','axios','request','requests','fetcher'}",
        "CLIENT_RECEIVERS={'client','http','https','axios','request','requests','fetcher'}",
    ),
    "Q2_typed_and_cross_file_router_alias_host_detection": (
        "(?:\\s*:\\s*[^=;\\n]+)?",
        "",
    ),
    "Q2_cross_file_exported_router_alias_host_detection": (
        "route_receivers.update(self._imported_route_aliases())",
        "pass",
    ),
    "Q3_simulate_change_vocabulary_and_self_loop_guard": (
        "if target == sim: continue",
        "if False: continue",
    ),
}


def run_suite(repo: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo / "src") + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"], cwd=repo, env=env, text=True, capture_output=True)


def failure_count(output: str) -> int:
    match = re.search(r"FAILED \(failures=(\d+)(?:, errors=(\d+))?\)", output)
    return sum(map(int, match.groups(default="0"))) if match else 0


def show(label: str, result: subprocess.CompletedProcess[str]) -> None:
    output = result.stdout + result.stderr
    print(f"{label}: EXIT={result.returncode} FAILURES={failure_count(output)}")
    print(output, end="" if output.endswith("\n") else "\n")


def main() -> None:
    selected=PROBES.items()
    if len(sys.argv) == 2:
        if sys.argv[1] not in PROBES:
            raise SystemExit(f"unknown probe: {sys.argv[1]}")
        selected=[(sys.argv[1],PROBES[sys.argv[1]])]
    with tempfile.TemporaryDirectory(prefix="archgraph-q1-q3-probes-") as directory:
        work = Path(directory) / "repo"
        shutil.copytree(PROJECT, work, ignore=shutil.ignore_patterns(".venv-codex", "__pycache__", "*.db"))
        for name, (before, after) in selected:
            source = work / "src" / "archgraph" / "indexer.py" if name.startswith("Q") and not name.startswith("Q3") else work / "src" / "archgraph" / "service.py"
            pristine = source.read_text(encoding="utf-8")
            print(f"=== {name} ===")
            baseline = run_suite(work)
            show("BEFORE", baseline)
            if baseline.returncode:
                raise SystemExit(f"{name}: baseline unexpectedly failed")
            if pristine.count(before) != 1:
                raise SystemExit(f"{name}: expected one mutation anchor, got {pristine.count(before)}")
            source.write_text(pristine.replace(before, after), encoding="utf-8")
            broken = run_suite(work)
            show("AFTER", broken)
            if broken.returncode == 0 or failure_count(broken.stdout + broken.stderr) == 0:
                raise SystemExit(f"{name}: mutation did not make the suite red")
            source.write_text(pristine, encoding="utf-8")


if __name__ == "__main__":
    main()
