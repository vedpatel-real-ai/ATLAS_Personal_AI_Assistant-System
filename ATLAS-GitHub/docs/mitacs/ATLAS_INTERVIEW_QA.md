# A conversation about ATLAS

These answers describe the project in the present tense. Before using a first-person account of who built or ran something, fill in the private contribution worksheet.

### 1. What is ATLAS?

“ATLAS is a local assistant for a Mac. You can ask a question about the machine or a codebase in everyday language. For example, it can inspect a project and report which files import a particular module. What makes that answer useful is that there’s a function reading actual files behind it.”

Remember **local assistant · observable result**. The dependency scan covers supported internal imports. [Tool code](../../tools.py)

### 2. What problem motivated the project?

“The design starts with a practical question. If someone asks about a project, how can an assistant see enough of the actual project to give a useful answer? ATLAS lets ordinary programs gather that information and uses a local model where interpreting language helps. The same approach also lets you handle familiar commands cheaply.”

Remember **project access · selective model use**. Add a personal origin story only if it is yours to tell. [Project overview](../../README.md) · [Matcher](../../matcher.py)

### 3. What technologies does it use?

“The browser sends a request to FastAPI. I think of that as a web frontend calling a backend route. Python functions do the local work, SQLite stores records, and Ollama serves the language model. Someone familiar with PostgreSQL will recognize tables and queries in the storage layer. Here the database lives in a local file.”

Remember **HTTP interface · Python functions · SQLite**. The browser UI is lightweight HTML and JavaScript. [UI](../../chat.html) · [API](../../main.py) · [Database](../../storage.py)

### 4. Can you walk me through one request?

“Take ‘what’s my battery?’ The matcher recognizes the word, selects the battery function and supplies its arguments. The wrapper calls the function, records whether it succeeded and returns a result through the API. That example needs no model inference. If the OS cannot provide a battery reading, the tool can return an error.”

Remember **match · dispatch · report**. The automated integration test supplies the hardware reading. [Matcher](../../matcher.py) · [Execution](../../main.py) · [Fixture](../../tests/conftest.py)

### 5. Why use a local model?

“A local model lets the assistant interpret a request on the same computer as the project. It also turns memory into a real design constraint. A larger model shares the machine with the editor, browser and operating system. To decide what fits, I’d want to see latency, swap and pressure together on the target machine.”

Remember **local inference · shared memory**. A tool such as transcript retrieval can still contact the network. [Router](../../router.py) · [Model experiment](../verification/BENCHMARK_VERIFICATION.md)

### 6. Why not use AI for every command?

“Some phrases have an obvious interpretation. For ‘open VS Code,’ a simple pattern can choose the right function. Asking a model to make the same choice adds an inference call and the possibility of a wrong argument. The tradeoff is coverage. A pattern is easy to test, but a new phrasing can miss it.”

Remember **predictability · fallback**. Attachment metadata can also select reasoning before matching. [Patterns](../../matcher.py) · [Route labels](../../decision_engine.py)

### 7. What does tool calling mean here?

“The model can propose a small structure containing a tool name and arguments. A Python registry maps that name to a function. The server checks the proposed steps, applies the tool’s approval rule and dispatches it. A suggested action is only a proposal until the backend has handled it and returned an outcome.”

Remember **structured proposal · registry · result**. Tier labels alone cannot make every invocation safe. [Router](../../router.py) · [Dispatch](../../main.py)

### 8. What are the 24 tools?

“I group them by use. There are desktop actions, repository inspection, file management, clipboard operations and summarization. For example, one tool reads a ZIP and another searches saved summaries. There are 24 registered names. Twenty-two contain substantive implementations. Email delivery and stock pricing still return an unfinished status.”

Remember **registry count · useful examples · two placeholders**. A complete list follows the questions. [Registry](../../tools.py) · [Wrapper tests](../../tests/test_verification_contracts.py)

### 9. How does dependency analysis work?

“It treats files as points in a graph. When one file imports another file in the project, the analyzer draws an arrow between them. Python gives us a parser for reading imports. For JavaScript and TypeScript, the implementation recognizes common text patterns. An iterative graph walk then looks for paths that return to a file.”

Remember **internal imports · parser versus patterns · cycles**. Dynamic imports, aliases and large projects can exceed the analyzer’s coverage. [Graph code](../../tools.py) · [Graph fixtures](../../tests/test_dependency_graph.py)

### 10. How does ATLAS handle risky actions?

“If a proposed step needs approval, the server returns the steps and a token. That token is tied to the exact arguments and has a five-minute lifetime. When the user confirms, the backend checks and consumes it. If the proposed filename changes, the old approval no longer matches that operation.”

Remember **exact steps · expiry · one use**. The local user’s permissions still govern what tools can access. [Token implementation](../../storage.py) · [Confirmation tests](../../tests/test_storage_confirmations.py)

### 11. How was ATLAS tested?

“The original suite collected 355 cases and all passed in the isolated rerun. With 23 regression cases, the final local total was 378. Some tests create actual temporary Git repositories, files and SQLite databases. Others use controlled replies from Ollama or the clipboard. The evidence page links to each case and the execution reports.”

Remember **355 original · 378 final · real and substituted boundaries**. Test results do not measure the quality of generated answers. [Test evidence](../verification/TEST_VERIFICATION.md)

### 12. What was the question behind comparing 4B and 8B?

“The question was whether a larger model could run comfortably on a 16 GiB Mac under the same workload. The recent experiment found both models installed. It finished a 4B warm-up, then stopped when macOS reported warning-level memory pressure. There’s no measured 8B trial, so I’d describe the comparison as open.”

Remember **same workload · resource stop · insufficient evidence**. Historical selection has no retained paired raw data. [Raw warm-up](../../evidence/benchmarks/2026-11-19/raw-results.json) · [Interpretation](../verification/BENCHMARK_VERIFICATION.md)

### 13. What did the document experiment teach you?

“One result I’d highlight is the difference between what an application sends and what a model evaluates. The July report recorded about 71.5 percent fewer prompt characters after the document was capped. Yet both paths reported 2,050 evaluated prompt tokens, and the shorter input took longer in that example. Choosing the right measurement changes the conclusion.”

Remember **characters · evaluated tokens · elapsed time**. The comparison comes from the retained report, whose named raw files are missing. [Historical report](../benchmark-report.md) · [Token probe](../../probe_tool_tokens.py)

### 14. What limitations would you investigate next?

“I’d build a small labelled collection of realistic requests. For each request I’d record the expected route, tool arguments and whether approval should be required. That would make it possible to count routing mistakes. For summaries I’d use a different evaluation, checking whether important claims are supported by the source text.”

Remember **labelled requests · argument correctness · grounded summaries**. These are proposed experiments. [Current router tests](../../tests/test_router.py) · [Claim matrix](../verification/CLAIM_MATRIX.md)

### 15. How does this connect to agentic AI research?

“ATLAS gives me a concrete system for discussing who proposes an action, who approves it and how we observe its outcome. A model can suggest a sequence, the backend can execute it, and a person can review sensitive steps. A research question would be how well the proposed actions match developer intent and when human review changes the outcome.”

Remember **proposal · human review · evaluation**. The planner executes a flat sequence and has no autonomous replanning loop. [Planner](../../planner.py) · [Research connections](PROJECT_CONNECTIONS.md)

### All 24 registry names

| Area | Entries |
|---|---|
| Desktop and processes (8) | open_app, open_url, play_youtube_video, get_battery, open_project, run_localhost, run_shell_command, read_clipboard |
| Code and archives (5) | index_project, dependency_graph, git_query, search_code, read_zip |
| Files and clipboard output (6) | read_file, copy_file, move_file, rename_file, delete_file, write_clipboard |
| Knowledge (3) | summarize_document, summarize_youtube_video, search_knowledge |
| Unfinished (2) | send_email, get_stock_price |
