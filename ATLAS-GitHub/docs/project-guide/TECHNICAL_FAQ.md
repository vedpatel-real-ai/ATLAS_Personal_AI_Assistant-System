# Technical FAQ

## 1. What can I ask ATLAS about?

ATLAS can answer questions about things a program on your Mac can observe. For example, ask which files in a project depend on a particular module. The dependency tool reads local source files and returns the internal imports it could resolve. The files must be available to the server process. [Tool implementations](../../tools.py)

## 2. Which problem motivated the project?

A language model can explain what a circular import is from general knowledge. To identify one in *your* repository, something has to read the code. ATLAS gives that job to Python and uses local inference when it helps interpret the request. Selective inference also matters on a 16 GB laptop that runs an editor and a browser alongside the assistant. [Project background](../../../README.md#project-background--development) · [Matcher](../../matcher.py)

## 3. Which technologies handle a request?

The HTML and JavaScript frontend calls a FastAPI endpoint, much like a web app fetching JSON from its backend. Python runs the selected function, Ollama serves model requests over HTTP, and SQLite keeps application state. If you know PostgreSQL, SQLite tables and queries will look familiar. Here the database resides in a local file. [Browser](../../chat.html) · [HTTP API](../../main.py) · [Database](../../storage.py)

## 4. What happens when I ask “What's my battery?”

The matcher recognizes “battery” and returns a step naming `get_battery` with empty arguments. The executor looks up the function, calls it and records the result. The response includes the percentage and charging state when the OS provides them. The integration test supplies a known reading so its result is independent of the runner’s hardware. [Matcher](../../matcher.py) · [Dispatch](../../main.py) · [Hardware fixture](../../tests/conftest.py)

## 5. Why use a local language model?

Ollama lets the backend interpret text on the same computer that holds the project. Running a larger model also competes for memory with other processes. For example, the November 4B warm-up completed while host memory pressure rose to warning. Latency, swap and memory pressure all matter when asking whether a model fits a workload. A tool that fetches a YouTube transcript still needs network access. [Router](../../router.py) · [Model experiment](../verification/BENCHMARK_VERIFICATION.md)

## 6. When can ATLAS skip inference?

Known phrases can map directly to functions. “Open VS Code” selects `open_app` with a known application name. A pattern is easy to test, but unfamiliar wording can miss it and fall through to the router. Attachment metadata gets an earlier check and can select direct reasoning before the matcher runs. [Patterns](../../matcher.py) · [Decision rules](../../decision_engine.py) · [Matcher tests](../../tests/test_matcher.py)

## 7. What does tool calling mean here?

The model proposes names and arguments. For example, a step might name `read_file` and supply a path. The Python registry works like a JavaScript object mapping names to handlers. The backend looks up the name, checks whether the batch requires approval, normalizes parameters and calls the function. An unknown tool is reported as a failed step. Even a recognized proposal can fail if its arguments are wrong. [Router prompt](../../router.py) · [Executor](../../main.py) · [Parameter tests](../../tests/test_normalize_params.py)

## 8. What do the 24 registry entries cover?

Seven entries handle desktop and process actions, five inspect repositories and archives, seven manage files or the clipboard, and three handle summaries and retrieval. For example, `read_zip` lists archive information without unpacking it, while `search_knowledge` finds previously stored summaries. The remaining two names, `send_email` and `get_stock_price`, return `not_implemented`. The wrapper reports those outcomes as unsuccessful. [Complete tool list](../../../README.md#tool-capabilities) · [Registry](../../tools.py) · [Result tests](../../tests/test_verification_contracts.py)

## 9. How does dependency analysis work?

Picture a file as a point and an import as an arrow. If `api.py` imports `storage.py`, the arrow goes from `api.py` to `storage.py`. Python’s parser produces an abstract syntax tree (AST), which gives the tool structured import statements. JS/TS support uses text patterns for common imports and `require` calls. The analyzer resolves internal targets and walks the resulting graph with an explicit stack. An edge back to a file on the current path closes a cycle.

A focused lookup can show incoming and outgoing edges for one file. Aliases, dynamic imports and toolchain-specific resolution can be missed. The scan also caps the number and size of files considered. [Analyzer](../../tools.py) · [Graph fixtures](../../tests/test_dependency_graph.py)

## 10. How does ATLAS protect sensitive operations?

A caution or dangerous step pauses the whole batch for review. For a rename, the server returns the proposed filename and replacement name, along with a token. SQLite stores a hash of the steps, an expiry and whether the token was used. Before execution the server checks those fields and consumes the token. Changing the proposed name changes the hash. The token expires after five minutes. The shared API key grants access to the service, and the underlying OS account determines what files it can reach. [Server gate](../../main.py) · [Token storage](../../storage.py) · [Lifecycle tests](../../tests/test_storage_confirmations.py)

## 11. What did the automated tests exercise?

The preserved original suite passed 355 of 355 on November 19, 2026. Twenty-three regression cases brought the suite to 378, all of which passed locally and in hosted macOS CI. A graph test can create a real pair of files and assert a specific edge. An Ollama test can supply a fixed reply and assert that the router interprets it correctly. The latter measures the program’s handling of the reply. Live model accuracy needs its own evaluation. [Test inventory, reports and hosted run](../verification/TEST_VERIFICATION.md)

## 12. What can we conclude about 4B versus 8B?

The experiment asked whether both models could run the same tasks within one laptop’s memory budget. It found both tags installed and recorded a single 4B warm-up. macOS then reported warning-level memory pressure, triggering the stop condition. There are zero measured repetitions for either model. A performance ranking or a claim that benchmarks selected the default model would require paired observations. [Raw warm-up](../../evidence/benchmarks/2026-11-19/raw-results.json) · [Protocol and limits](../verification/BENCHMARK_VERIFICATION.md)

## 13. Why did a shorter document prompt still evaluate the same number of tokens?

Prompt characters count what the application sends. Ollama’s evaluated-token counter measures what the inference service reports evaluating. The July report recorded about 71.5% fewer characters after applying a document excerpt limit, yet both paths reported 2,050 evaluated tokens. The shorter input also took longer in that example. The retained measurements do not identify the cause. Testing context limits or caching would need a controlled follow-up. [July report](../benchmark-report.md) · [Token probe](../../probe_tool_tokens.py)

## 14. How could routing and summary quality be measured?

Start with requests whose expected route, arguments and approval requirement are known. For “rename draft.md to notes.md,” score the selected tool and both names independently. For summaries, compare individual claims with the source excerpt the model actually saw. These are proposed evaluations. Current router fixtures check how the software handles supplied model replies. [Router tests](../../tests/test_router.py) · [Open evaluation questions](EXAMPLES_AND_LIMITATIONS.md)

## 15. What can ATLAS teach us about human oversight of agents?

ATLAS makes it possible to follow a proposed action through human approval to a recorded outcome. For example, a researcher could compare the filename a developer intended with the one shown for approval and record whether the developer caught a mismatch. The planner executes a flat sequence with limited retries. It has no autonomous replanning loop, and the project has no completed study of approval behavior. [Planner](../../planner.py) · [Approval interface](../../chat.html) · [Design decisions](DESIGN_DECISIONS.md)
