# Running ATLAS on macOS

ATLAS serves a browser chat and a FastAPI API on loopback. Run the server from this directory so it can find `chat.html`. For the project motivation, architecture diagram and evidence summary, see the [repository overview](../README.md).

## Install the application

Install Git, Python 3.12 and [Ollama for macOS](https://ollama.com/download/mac). Repository search also uses the `rg` executable. Opening a project in VS Code requires its `code` command on your path. Other tools can run without those optional programs.

```sh
git clone https://github.com/vedpatel-real-ai/ATLAS_Personal_AI_Assistant-System.git
cd ATLAS_Personal_AI_Assistant-System/ATLAS-GitHub
python3.12 -m venv venv
. venv/bin/activate
python -m pip install -r requirements-test.txt
```

The tested environment includes the runtime packages and the HTTP client used by FastAPI’s tests. For application-only use, install `requirements.txt` in place of `requirements-test.txt`.

## Select an installed model

Open Ollama or run `ollama serve` in a separate terminal. The app expects its local HTTP API on port 11434. Run `ollama list` and look for an installed instruction model that can return JSON for routing. The default tag for all three calls is `qwen3:4b-instruct`. To use another installed model, copy its exact tag into these constants before starting ATLAS.

| Setting | Use |
|---|---|
| `ROUTER_MODEL` in [router.py](router.py) | Interpret requests and propose tool arguments |
| `CODER_MODEL` in [main.py](main.py) | Answer direct reasoning requests |
| `SUMMARIZE_REASONING_MODEL` in [tools.py](tools.py) | Summarize extracted documents and transcripts |

Restart the application after changing the settings. Model choice can affect routing and summary quality. The test suite runs without installing or loading any models.

## Start and try a request

```sh
bash start.sh
```

The launcher activates `venv`, generates a local key on first use, starts Ollama if needed and starts Uvicorn on `127.0.0.1:8420`. Open `http://127.0.0.1:8420/`, select **API key** and enter the contents of `.secrets/api_key` locally. The UI remembers the key in that browser’s local storage. Try **What's my battery?** to exercise a deterministic route. Look for the reported charging state or the tool’s error if the hardware reading is unavailable.

Use `http://127.0.0.1:8420/docs` to inspect the API schema. Runtime logs are under `logs/`. The `atlas.db` database and event log can contain prompts, paths and results. Keep the service on loopback and use one worker. [Security and authentication](SECURITY.md)

`bash status.sh` shows process and port information. The bundled `stop.sh` and the UI shutdown action stop matching Uvicorn processes and Ollama by process name. On a machine sharing those services, run Uvicorn in the foreground instead and stop that instance with Ctrl-C. From this directory with the environment active and an existing generated key, use the following command.

```sh
export ASSISTANT_API_KEY="$(cat .secrets/api_key)"
python -m uvicorn main:app --host 127.0.0.1 --port 8420
```

## API behavior

| Endpoint | Behavior |
|---|---|
| `POST /command` | Authenticates, classifies and returns a text response, tool results, a confirmation request or an error. |
| `POST /command/async` | Classifies inline, then returns a job ID for background execution or a confirmation request for sensitive steps. |
| `POST /jobs/{job_id}/confirm` | Verifies the token against the stored steps and starts the approved job. |
| `GET /jobs/{job_id}`, `GET /jobs` | Read job progress or list recent jobs. |
| `POST /jobs/{job_id}/cancel` | Requests cancellation. Work already in a step can continue. |

Command, job and shutdown requests need the local key in the `X-API-Key` header. A missing server configuration produces HTTP 503. Missing or incorrect request credentials produce HTTP 401. A successful HTTP exchange can still contain a tool failure. Inspect the returned type and per-step outcomes. [Handler](main.py) · [Result and authentication tests](tests/test_verification_contracts.py)

## Run the tests

With the test dependencies installed and the virtual environment active:

```sh
python -m pytest -q tests
```

To export a transcript, JUnit and a manifest for the **committed** revision, use a new output directory. The exporter copies the Git snapshot to temporary storage before running pytest.

```sh
python scripts/verify_tests.py /tmp/atlas-test-evidence
```

See the [test inventory](docs/verification/TEST_VERIFICATION.md) for fixture boundaries and the [benchmark guide](docs/verification/BENCHMARK_VERIFICATION.md) for the separate local-inference experiment.

## Reading the code

- [Request flow](docs/project-guide/HOW_IT_WORKS.md) follows the browser, matcher, router and registry.
- [Design decisions](docs/project-guide/DESIGN_DECISIONS.md) explains persistence, graph traversal and approval.
- [Source-code verification](docs/verification/CLAIM_MATRIX.md) links individual claims to implementations and tests.
- [Contributing](CONTRIBUTING.md) · [MIT license](LICENSE)
