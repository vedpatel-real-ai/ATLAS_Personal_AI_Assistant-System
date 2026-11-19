# Three ways to introduce ATLAS

### About 20 seconds

“ATLAS is a local Mac assistant that connects everyday language to Python tools. It can answer a battery question with ordinary code or inspect the imports in a project. What interests me is the boundary between a model interpreting a request and a program doing work that someone can check.”

### About 45 seconds

“ATLAS explores how a local assistant can help with real development tasks. If you ask which files depend on a module, the answer can come from inspecting the repository. For a familiar phrase, a pattern can select the tool directly. For other requests, a small local language model can propose a tool and its arguments. The backend handles execution and asks for confirmation on sensitive actions. I like this example because I can follow a request from the browser to a database record and then show the test that checks that behavior.”

### About 90 seconds

“ATLAS is a local assistant for a Mac. The problem it explores is how to turn a sentence about a real project into something useful and verifiable. For example, a language model can explain what a circular import is from general knowledge. To tell you whether two files in your project import each other, it needs access to the project.

“ATLAS connects those pieces. A small web interface sends a request to FastAPI. Familiar commands can go straight through a pattern matcher. When a request needs interpretation, a local model can suggest a tool or a short sequence of tools. Python handles execution. The code-analysis tool parses Python imports and builds a graph, so you can inspect the edges behind an answer. For actions like renaming a file, the server also records which steps the user was asked to approve.

“The experiments gave me a good example of how careful you have to be with metrics. A past document report described a much shorter prompt in characters, but the model reported the same evaluated token count. In a new local model experiment, memory pressure rose during the first warm-up, so the comparison stopped. Those are different kinds of evidence. The repository makes the raw observations, test reports and remaining questions available to inspect.”

[Implementation claims](../verification/CLAIM_MATRIX.md) · [Benchmark observations](../verification/BENCHMARK_VERIFICATION.md) · [Contribution worksheet](CONTRIBUTIONS.md)
