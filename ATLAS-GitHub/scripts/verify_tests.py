"""Run pytest on committed sources in a disposable checkout and export evidence."""
import argparse
import hashlib
import importlib.metadata as metadata
import io
import json
import os
import platform
import re
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parents[1]


def git(*args):
    return subprocess.check_output(["git", "-C", str(APP), *args])


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="new evidence directory")
    parser.add_argument("--revision", default="HEAD")
    args = parser.parse_args()
    revision = git("rev-parse", "--verify", args.revision + "^{commit}").decode().strip()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    started = datetime.now(timezone.utc).isoformat()
    env_data = dict(python=platform.python_version(), pytest=metadata.version("pytest"),
                    os=platform.system(), release=platform.release(), macos=platform.mac_ver()[0],
                    architecture=platform.machine(),
                    packages={p.metadata["Name"]: p.version for p in metadata.distributions()})
    write_json(out / "environment.json", env_data)
    with tempfile.TemporaryDirectory(prefix="atlas-verify-") as scratch:
        work = Path(scratch)
        archive = git("archive", "--format=tar", revision, ".", ":!evidence")
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            tar.extractall(work, filter="data")
        hashes = {str(p.relative_to(work)): sha(p) for p in sorted(work.rglob("*")) if p.is_file()}
        env = {k: os.environ[k] for k in ("PATH", "SYSTEMROOT", "LANG") if k in os.environ}
        env.update(HOME=scratch, TMPDIR=scratch, USER="atlas-test", LOGNAME="atlas-test", PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
                   PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1")
        commands = {}
        exits = {}
        for label, options in (("collection", ["--collect-only", "-q"]),
                               ("pytest-output", ["-v", "--junitxml=junit.xml"])):
            cmd = ["-m", "pytest", "--basetemp=.pytest-tmp", "--color=no", "--no-header", *options, "tests"]
            commands[label] = ["python", *cmd]
            result = subprocess.run([sys.executable, *cmd], cwd=work, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            exits[label] = result.returncode
            raw = result.stdout
            for path, marker in ((scratch, "<scratch>"), (str(Path.home()), "<home>"),
                                 (sys.prefix, "<venv>")):
                raw = raw.replace(path, marker)
            (out / (label + ".txt")).write_text(raw)
        suite_counts = dict(tests=0, failures=0, errors=0, skipped=0)
        durations = []
        xml = work / "junit.xml"
        if xml.exists():
            tree = ET.parse(xml)
            for suite in tree.iter("testsuite"):
                suite.attrib.pop("hostname", None)
                for key in suite_counts:
                    suite_counts[key] += int(suite.get(key, 0))
                durations.append(float(suite.get("time", 0)))
            report = ET.tostring(tree.getroot(), encoding="unicode")
            for path, marker in ((scratch, "[scratch]"), (str(Path.home()), "[home]"),
                                 (sys.prefix, "[venv]")):
                report = report.replace(path, marker)
            (out / "junit.xml").write_text(report + "\n")
    collection = (out / "collection.txt").read_text()
    collected = sum("::" in s and s.startswith("tests/") for s in collection.splitlines())
    terminal = (out / "pytest-output.txt").read_text()
    last = terminal.splitlines()[-1] if terminal else ""
    counts = {key: int(match.group(1)) if (match := re.search(r"(\d+) " + key + r"\b", last)) else 0
              for key in ("passed", "failed", "skipped", "xfailed", "xpassed", "deselected",
                          "warnings?", "errors?")}
    counts["warnings"] = counts.pop("warnings?")
    counts["errors"] = counts.pop("errors?")
    verified = (not any(exits.values()) and collected > 0 and collected == suite_counts["tests"]
                and collected == counts["passed"])
    manifest = dict(commit=revision, started_utc=started,
                    ended_utc=datetime.now(timezone.utc).isoformat(), commands=commands,
                    environment=env_data, source_sha256=hashes, exit_codes=exits,
                    collected=collected, results=counts, junit=suite_counts,
                    duration_s=sum(durations), all_collected_passed=verified,
                    boundaries="See tests/conftest.py and individual test doubles. In-process HTTP, disposable SQLite and Git. No live Ollama, editor, clipboard, email or transcript service.",
                    redactions="Temporary and user paths normalized, JUnit hostname removed.")
    write_json(out / "manifest.json", manifest)
    summary = (f"# Pytest verification\n\nRevision `{revision}` on {started} (UTC). "
               f"{env_data['os']} {env_data['release']} {env_data['architecture']}, "
               f"Python {env_data['python']}, pytest {env_data['pytest']}.\n\n"
               f"Collected {collected}. {last.strip('= ')}. "
               f"Collection exit {exits['collection']}, execution exit {exits['pytest-output']}.\n")
    (out / "test-summary.md").write_text(summary)
    (out / "checksums.sha256").write_text("".join(
        f"{sha(p)}  {p.name}\n" for p in sorted(out.iterdir())))
    print(summary)
    return 0 if verified else 1


if __name__ == "__main__":
    sys.exit(main())
