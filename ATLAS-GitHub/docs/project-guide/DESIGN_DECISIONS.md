# Design decisions

| Engineering question | Choice and tradeoff |
|---|---|
| How should a browser reach local functions? | FastAPI accepts typed HTTP requests and returns JSON. Python puts OS calls, file parsing and tool functions in one backend. Local tools inherit that backend’s OS permissions. [API](../../main.py) · [Tools](../../tools.py) |
| When is a model worth invoking? | A deterministic pattern handles familiar commands cheaply and makes exact behavior testable. On a miss, an Ollama model gets tool names and parameter signatures. This accommodates varied phrasing at the cost of inference time and occasional incorrect arguments. [Matcher](../../matcher.py) · [Router](../../router.py) · [Parameter correction](../fix-notes.md) |
| Where should state live? | SQLite provides durable, queryable tables without a separate database server. That suits a single-user application. A stored project path or summary can be retrieved after restart. Worker threads cannot. [Schema and queries](../../storage.py) · [Jobs](../../jobs.py) |
| How can the tool explain a dependency? | Python’s AST distinguishes import statements from comments and strings. JS/TS patterns provide narrower coverage without requiring a project compiler. Only imports resolved to project files contribute graph edges. [Graph builder](../../tools.py) |
| Why use iterative depth-first search? | An explicit stack can follow a long import chain without using one Python call frame per file. In cycle enumeration the active path distinguishes an edge back to an ancestor from two independent files importing the same library. A separate focused walk checks one file even if the list of example cycles is full. [Traversal](../../tools.py) · [Cycle cases](../../tests/test_dependency_graph.py) |
| What does approval authorize? | A server-issued token lets the backend check the complete set of proposed tool names and arguments, its expiry and prior use. One approval covers the entire sensitive batch. Tier assignment and a person’s understanding of the proposed arguments still matter. [Confirmation storage](../../storage.py) · [Server checks](../../main.py) |
| How much planning happens? | A flat, ordered plan makes attempts and outcomes inspectable. Only the designated video playback tool is eligible for one retry. There is no general loop that observes a failure and invents a new plan. [Planner](../../planner.py) · [Planner tests](../../tests/test_planner.py) |

The [source-code verification matrix](../verification/CLAIM_MATRIX.md) connects each major implementation claim to code or tests.
