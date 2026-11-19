# Contributing to ATLAS

Thanks for helping improve ATLAS.

## Development

From the application directory, create an isolated environment and install the test dependencies.

```bash
python3.12 -m venv venv
source venv/bin/activate
python -m pip install -r requirements-test.txt
```

Run the test suite:

```bash
pytest -q
```

## Before opening a pull request

- Add or update tests for behavioral changes.
- Do not commit secrets, runtime databases, logs, local paths, virtual environments, or generated caches.
- Keep benchmark claims reproducible and clearly distinguish measured results from interpretation.
- Keep changes focused and document security-sensitive behavior.
