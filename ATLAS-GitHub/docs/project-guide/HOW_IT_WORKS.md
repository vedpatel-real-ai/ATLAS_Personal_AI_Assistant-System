# How ATLAS handles a request

Imagine asking “What's my battery?” in a web page. A plausible number is easy to generate. A useful answer comes from reading the machine. Follow that request from the page to its result, then see where a less predictable request takes a different path.

## 1. Receive the request

The browser sends JSON to `POST /command`, like a frontend calling an API route. The API key travels in a header. FastAPI checks it, applies the rate limit and builds a request context with the text, attachment metadata and state recalled from SQLite. For example, the last project visited can supply a missing directory for an index or dependency scan. [Chat client](../../chat.html) · [API](../../main.py) · [Request context](../../request_context.py)

## 2. Choose how to interpret it

For the battery question, a regular expression produces a step equivalent to `{"tool": "get_battery", "params": {}}`. No model runs. On a miss, the router sends the text and tool signatures to Ollama and asks for JSON. The model can propose one or several steps, or request a direct text answer. The route label records that choice.

One check comes before matching. A ZIP or PDF attachment descriptor, or one with `size_mb >= 1`, routes directly to reasoning. That branch sends the command text to the model. A document tool, in contrast, opens a local path and extracts content. [Matcher](../../matcher.py) · [Router](../../router.py) · [Attachment rules](../../decision_engine.py)

## 3. Look up the function and check approval

Think of the tool registry as an object mapping names to handlers. Each entry supplies a Python function, description and safety tier. A battery lookup can run immediately. A batch containing a rename pauses before its first step. ATLAS returns the names and arguments for review, and SQLite holds a five-minute, single-use approval token bound to those steps. The synchronous endpoint classifies the request again on confirmation. A changed sensitive proposal needs a matching approval. [Registry](../../tools.py) · [Approval checks](../../storage.py) · [Confirmation tests](../../tests/test_storage_confirmations.py)

## 4. Execute and report the result

The planner walks the steps in order. The wrapper normalizes arguments, calls the selected function and records timing, process memory and success. For the battery question the result contains a percentage and charging state, or an error if the information is unavailable. For a repository question the result might contain a graph: files are nodes and an arrow from A to B means A imports B. Following incoming arrows answers “who uses B?” [Executor](../../main.py) · [Planner](../../planner.py) · [Graph tests](../../tests/test_dependency_graph.py)

## 5. Keep state between requests

Like a backend using PostgreSQL, ATLAS stores rows and queries them later. SQLite keeps its tables in a local file. Project visits, preferences, conversations, logs, jobs and summaries have different tables and purposes. A remembered project can help resolve a path, and keyword search can retrieve a stored summary. Direct reasoning receives the command text. The existence of conversation history does not automatically put it in the model prompt. [Memory helpers](../../memory.py) · [Storage](../../storage.py) · [Reasoning call](../../main.py)

An API client can also submit to `/command/async`. Classification happens inline. A worker runs the selected tools or direct reasoning, and the client polls the job ID for progress. Cancellation is checked between steps. After a restart, the record survives and the server marks abandoned work failed. [Jobs](../../jobs.py) · [Async endpoint tests](../../tests/test_async_command.py)
