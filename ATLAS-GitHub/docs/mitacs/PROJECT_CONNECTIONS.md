# Connecting ATLAS to four Mitacs choices

These are conversation starters based on the supplied project titles. The proposed questions describe possible research directions.

| Project | What ATLAS lets you demonstrate | A question to bring to the conversation |
|---|---|---|
| Ali Ouni — Enhancing Software Development Workflows with Agentic AI | Trace a structured tool proposal through approval, execution and progress reporting. Show how a repository scan grounds a developer-facing answer. [Planner](../../planner.py) · [Analyzer tests](../../tests/test_dependency_graph.py) | “How would you evaluate whether a tool-using assistant helps developers complete a real task correctly?” |
| Ronnie de Souza Santos — Human–AI Collaboration in Software Development | A short proposed action list and a server-issued approval token give a concrete example of a handoff from model to human. ATLAS has no user study measuring whether the prompt helps. [Confirmation interface](../../chat.html) · [Server checks](../../storage.py) | “What would you observe to tell whether a developer understands the action they are approving?” |
| Elyes Manai — ResOps Research Management Platform | SQLite records run identifiers, measurements and jobs. Reproducible test exports show how another researcher can inspect outcomes. These are small-scale analogues to tracking research work. [Schema](../../storage.py) · [Evidence exporter](../../scripts/verify_tests.py) | “Which provenance fields matter most when a second researcher needs to repeat a run?” |
| Fabio Petrillo — Machine Learning for Noise in Log Files | ATLAS logs route, tool, success and timing. A discussion can start with how to represent failures and separate repeated symptoms from useful events. ATLAS has no trained log-noise classifier or labelled log corpus. [Event storage and failure queries](../../storage.py) | “How would you label noisy events while preserving rare failures that are important to diagnose?” |
