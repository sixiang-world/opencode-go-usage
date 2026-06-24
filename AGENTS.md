# Repository Guidelines

## Project Structure & Module Organization

```
opencode-go-usage/
├── AGENTS.md                    # This file
├── SKILL.md                     # Codex skill definition for agent usage
├── requirements.txt             # Python runtime dependencies
├── scripts/
│   └── opencode-go-usage.py     # Main CLI entry point (~42 KB)
├── references/
│   └── usage-page-ssr-format.md # SolidJS SSR data format reference
└── .gitignore
```

- **`scripts/opencode-go-usage.py`** — single-file CLI with `asyncio` + `httpx` + `rich`.
  Internal modules are organized by function: session/auth, SSR parsing, aggregation, CLI dispatch.
- **`references/`** — reverse-engineering notes for the OpenCode `/usage` page's SolidJS SSR payload.
  Update this file whenever the upstream SSR format changes.
- **`SKILL.md`** — used by Codex agents to discover and invoke this tool. Keep its trigger phrases,
  usage examples, and JSON schema in sync with the CLI.

## Build, Test, and Development Commands

This project has no formal build step. All commands use `uv` for dependency management:

```bash
# Run the CLI with rich terminal output
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py

# Query with JSON output (for scripts / cron)
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py --json

# Show usage history (last 20 entries)
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py history

# Daily cost aggregation
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py costs
```

### Ad-hoc testing

There is no test framework. Validate changes by running a real query against a workspace
(non-destructive, read-only) and diffing the JSON output against an expected structure:

```bash
uv run --with-requirements requirements.txt scripts/opencode-go-usage.py --json \
  | python3 -m json.tool
```

## Coding Style & Naming Conventions

- **Indentation:** 4 spaces. No tabs.
- **Line length:** Aim for ≤ 100 characters.
- **Naming:** `snake_case` for variables, functions, and modules. `UPPER_CASE` for constants.
- **Types:** Use Python `typing` annotations (`from __future__ import annotations` style).
- **Imports:** Standard library → third-party → local, separated by blank lines.
- **CLI dispatch:** Subcommands are implemented as async functions with a shared `Console` and
  `httpx.AsyncClient` context. Avoid adding global state.

## Testing Guidelines

- There is no test runner configured. All validation is performed through manual execution
  against the live API.
- When modifying the SSR parsing logic (`_parse_ssr_script`), capture a real page response
  and save it in `references/` as a test fixture, then verify extraction against it.
- Commit messages (see below) serve as the primary regression log.

## Commit & Pull Request Guidelines

This project follows [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add costs command for daily cost aggregation
fix(security): mask workspace_id, history chmod, specific exceptions
refactor: fetch_usage_records + enrich_records + query_type validation
chore: add .codex to gitignore
```

- **Scope** (optional, in parentheses) for security-sensitive or subsystem-specific changes.
- **Imperative mood**, lowercase, no trailing period.
- PR descriptions should include a before/after JSON diff when changing output format, and a
  note about which workspace the change was tested against.

## Agent-Specific Instructions

This repository is designed to be operated by Codex agents via the [Codex skill system](https://github.com/codex/agent-sdk).

- **`SKILL.md`** is the agent-facing manifest. Its `trigger` field must match the natural-language
  patterns that should invoke this tool (e.g., "opencode go 用量", "go usage").
- **Do not** hardcode credentials in source files. Authentication flows through
  `~/.opencode-go-usage.json` or environment variables (`OPENCODE_GO_WORKSPACE_ID`,
  `OPENCODE_GO_AUTH_COOKIE`).
- **Dependency pinning:** Update `requirements.txt` when bumping `httpx` or `rich`; the
  `--with-requirements` flag in the agent's invocation command uses this file.
- When the upstream OpenCode page layout or SSR format changes, update both the parsing logic
  in `scripts/opencode-go-usage.py` and the reference document in `references/`.

## Security & Configuration Tips

- **Auth cookie** (`~/.opencode-go-usage.json`): `chmod 600` after save. The CLI enforces this.
- **Workspace ID** is a sensitive identifier — avoid logging it verbatim. When adding new output
  formats or export commands, ensure `workspace_id` is masked by default.
- **`history` subcommand** appends to `~/.opencode-go-usage-history.jsonl`. This file contains
  timestamps and usage data but should never contain the raw auth cookie. Verify after any
  structural change.
