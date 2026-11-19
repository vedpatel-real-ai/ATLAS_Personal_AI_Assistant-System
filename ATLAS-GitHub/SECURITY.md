# Security Policy

## Scope

ATLAS can execute local tools and interact with files and processes. Security issues affecting command execution, authentication, confirmation-token handling, path restrictions, or unintended data exposure are especially important.

## Reporting a vulnerability

Please do not publish working exploit details in a public issue. Open a private security report through GitHub's supported security-reporting mechanism for the repository, or contact the repository maintainer privately before disclosure.

## Local deployment guidance

- Keep ATLAS bound to `127.0.0.1` unless you add authentication and network controls.
- Never commit `.secrets/`, `atlas.db`, logs, or real benchmark captures containing private data.
- Treat any API key that has appeared in source control or an uploaded archive as compromised and rotate it.
- Review tool allowlists and confirmation logic before enabling new executable actions.

## Development authentication

Start the app on loopback with `start.sh`, which generates a local key. For manual Uvicorn use, set a nonblank `ASSISTANT_API_KEY` before starting the process and send its value in `X-API-Key`. Command, job and shutdown endpoints return 503 when configuration is missing and 401 for invalid credentials. The browser UI and API schema are public. Development uses the same credential check. Logs and model output can contain personal data.
