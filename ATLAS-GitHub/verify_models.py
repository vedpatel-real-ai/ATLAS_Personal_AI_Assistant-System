"""Bounded local timing experiment. Only the fixed public prompts reach Ollama."""
import argparse
import csv
import hashlib
import json
import platform
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import psutil
import requests

from benchmark_models import DEFAULT_PROMPTS, run_trial
from system_snapshot import capture_snapshot

MODELS = ["qwen3:4b-instruct", "qwen3:8b"]
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 2048, "num_predict": 64}
TASKS = [item for item in DEFAULT_PROMPTS if item[0] in ("tool_style", "code_reasoning", "summarization")]
MIB = 1024 ** 2


def now():
    return datetime.now(timezone.utc).isoformat()


def save(out, name, data):
    p = out / name
    temp = p.with_suffix(".tmp")
    temp.write_text(json.dumps(data, indent=2) + "\n")
    temp.replace(p)


def snapshot(label):
    data = capture_snapshot(label)
    mem = psutil.virtual_memory()
    data.update(total_mib=mem.total / MIB, available_mib=round(mem.available / MIB, 1))
    return data


def get(base, path):
    r = requests.get(base + path, timeout=5)
    r.raise_for_status()
    return r.json()


def run(out, base, order):
    env = dict(started_utc=now(), macos=platform.mac_ver()[0], architecture=platform.machine(),
               python=platform.python_version(), ollama=None,
               order=order, snapshots=[], status="preflight")
    rows = []
    try:
        env["ollama"] = get(base, "/version")["version"]
        inventory = {m["name"]: m for m in get(base, "/tags")["models"]}
        models = {tag: {k: inventory[tag].get(k) for k in ("name", "digest", "size", "details")}
                  for tag in order if tag in inventory}
        save(out, "model-configurations.json", dict(models=models, requested_options=OPTIONS,
             think=False, tasks=TASKS, repetitions=3, warmups_per_model=1))
        if len(models) != len(order):
            raise RuntimeError("Requested tag missing locally")
        for tag in order:
            before = snapshot("before " + tag)
            env["snapshots"].append(before)
            if get(base, "/ps")["models"]:
                raise RuntimeError("Endpoint already has a resident model")
            # Conservative headroom screen, not a prediction of runtime allocation.
            if before["memory_pressure"] != "normal" or before["available_mib"] < models[tag]["size"]/MIB + 1024:
                raise RuntimeError(f"Resource preflight stopped before loading {tag}")
            trials = [("warmup", 0, "warmup", "Reply with the word ready.")]
            trials += [("measured", rep, label, prompt) for rep in range(1, 4) for label, prompt in TASKS]
            for index, (phase, rep, label, prompt) in enumerate(trials):
                check = snapshot("pre-trial")
                if check["memory_pressure"] != "normal" or check["available_mib"] < 1024:
                    raise RuntimeError(f"Resource check stopped {tag} before {phase} {rep} {label}")
                start = now()
                # Release the model at block end. On interruption the short TTL expires.
                ttl = "0" if index == len(trials)-1 else "20s"
                result = run_trial(tag, label, prompt, base + "/generate", ttl, 60,
                                   think=False, options=OPTIONS)
                end = now()
                after = snapshot("post-trial")
                row = dict(phase=phase, repetition=rep, started_utc=start, ended_utc=end,
                           **result, before=check, after=after)
                rows.append(row)
                save(out, "raw-results.json", rows)
                if not result["success"] or after["memory_pressure"] != "normal":
                    raise RuntimeError(f"Trial or pressure check stopped {tag} after {phase} {rep} {label}")
            env["snapshots"].append(snapshot("after " + tag))
        env["status"] = "completed"
    except (RuntimeError, requests.RequestException) as exc:
        env["status"] = str(exc)
    finally:
        env["ended_utc"] = now()
        save(out, "environment.json", env)
        if rows:
            fields = sorted(set().union(*(r.keys() for r in rows)) - {"before", "after"})
            with (out / "raw-results.csv").open("w", newline="") as f:
                writer = csv.DictWriter(f, fields, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
        lines = ["# Benchmark result", "", env["status"], "",
                 "| Model | Completed measured trials | Failed attempts (incl. warm-up) | Mean latency (s) | Median latency (s) | Mean trial output tokens/s |",
                 "|---|---:|---:|---:|---:|---:|"]
        for tag in order:
            completed = [r for r in rows if r["model"] == tag and r["phase"] == "measured" and r["success"]]
            failures = sum(not r["success"] for r in rows if r["model"] == tag)
            lat = [r["latency_s"] for r in completed]
            rates = [r["eval_tokens_per_s"] for r in completed if r.get("eval_tokens_per_s") is not None]
            metrics = [round(statistics.mean(lat), 2) if lat else "N/A",
                       statistics.median(lat) if lat else "N/A", round(statistics.mean(rates), 2) if rates else "N/A"]
            lines.append(f"| {tag} | {len(lat)} | {failures} | " + " | ".join(map(str, metrics)) + " |")
        (out / "benchmark-summary.md").write_text("\n".join(lines) + "\n")
        (out / "checksums.sha256").write_text("".join(
            f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.name}\n" for p in sorted(out.iterdir())))
    print(env["status"])
    return 0 if env["status"] == "completed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--port", type=int, default=11434)
    parser.add_argument("--reverse", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    sys.exit(run(args.output, f"http://127.0.0.1:{args.port}/api", MODELS[::-1] if args.reverse else MODELS))
