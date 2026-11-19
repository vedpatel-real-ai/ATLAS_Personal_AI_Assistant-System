# ATLAS

> A local macOS assistant for commands, project inspection and tool-assisted summarization.

ATLAS tries ordinary code first for familiar commands. Its router and direct reasoning paths both specify `qwen3:4b-instruct`. A registry maps 24 tool names to Python functions. Two entries, stock quotes and email delivery, return an unfinished status.

[Inspect the test and benchmark evidence](docs/verification/README.md).

Instead of sending every request directly to a large model, ATLAS combines deterministic matching, local model routing, tool execution, persistent memory, and explicit confirmation for potentially destructive actions.

## Highlights

- Deterministic request matching before model escalation
- Local LLM routing through Ollama
- Tool execution for project/file/system workflows
- Persistent SQLite-backed memory and job state
- Server-issued, one-time confirmation tokens for caution/dangerous actions
- Local FastAPI API and lightweight browser chat UI
- Repository inspection, dependency analysis, document summarization, and YouTube transcript workflows
- Benchmark tooling for measuring latency, capability, and context handling
- macOS-oriented startup/status/stop helpers

## Architecture

```text
User Request
     │
     ▼
Request Context ── ZIP/PDF or large attachment metadata ──► Direct reasoning
     │
     ▼
Deterministic Matcher
     │
     ├── Match found ─────────────► Tool Execution
     │
     ▼
Local Router Model
     │
     ├── Tool request ────────────► Tool Execution
     │
     └── Needs reasoning ────────► Direct reasoning
                                      │
                                      ▼
                                   Response
```

Potentially destructive operations are separated into safety tiers. Caution/dangerous steps require a server-issued confirmation token that is bound to the exact step list and expires after a short TTL.

## Requirements

- macOS
- Python 3.12+ recommended
- [Ollama](https://ollama.com/)
- Local models compatible with the Ollama configuration you choose

Install Python dependencies:

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Start the assistant:

```bash
chmod +x start.sh stop.sh status.sh
./start.sh
```

The local API is available at `http://127.0.0.1:8420/` and the FastAPI docs at `http://127.0.0.1:8420/docs`.

## Security and privacy

ATLAS executes local actions, so it should be treated as a local automation service rather than exposed directly to the public internet.

The public repository intentionally excludes runtime secrets, SQLite databases, logs, virtual environments, caches, and other machine-specific artifacts. `start.sh` generates a fresh local API key under `.secrets/api_key` on first run; that directory is gitignored.

Do not commit your local `.secrets/`, `atlas.db`, logs, or benchmark captures that contain private file paths or conversation data.

## Benchmarking

The benchmark documentation compares tool-assisted and direct LLM-only workflows across repository analysis, external content extraction, and document summarization.

The current evidence shows useful differences in capability and input handling, but the measurements are experimental. In particular, the document experiment should not be interpreted as a proven token-count reduction when the model-side token counters were equal.

See [`docs/benchmark-report.md`](docs/benchmark-report.md).

## Project status

ATLAS is an active experimental project. The codebase is intended for technical review, experimentation, benchmarking, and further development rather than production deployment.

## Repository layout

```text
.
├── main.py                 # FastAPI application and command handling
├── matcher.py              # Deterministic request matching
├── router.py               # Local model routing
├── tools.py                # Tool registry and tool implementations
├── planner.py              # Execution planning
├── decision_engine.py      # Action/safety decisions
├── storage.py              # SQLite persistence and confirmation tokens
├── memory.py               # Persistent memory helpers
├── jobs.py                 # Background jobs
├── request_context.py      # Request/context preparation
├── system_snapshot.py      # Local system inspection
├── chat.html               # Browser chat UI
├── tests/                  # Automated tests
└── docs/                   # Public project notes and benchmark report
```

## License

This repository is distributed under the MIT License. See [`LICENSE`](LICENSE).
