# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository status

Scaffolded with `agents-cli scaffold create` (v0.5.1, `--agent adk --prototype --region asia-northeast1`). Agent code lives in `app/` (`App(name="app")` must always match that directory name). `agents-cli-manifest.yaml` is CLI-managed — never edit it by hand. `tools/vertex-claude-usage/` is an unrelated pre-existing utility; leave it alone.

Key runtime facts:
- Model: `gemini-3.5-flash` via Vertex AI (`GOOGLE_CLOUD_LOCATION=global` is bootstrapped in `app/agent.py`; the `gemini-flash-latest` alias does NOT exist in this project — don't revert to it).
- Firestore: named database `sre-agents` in `asia-northeast1` (collections `support_cases`, `incident_logs`); seed data via `make seed`.
- Remediation tools are deterministic mocks (`app/tools/remediation_tools.py` is the swap-point for real implementations).
- **HITL gating** (two gates, same pattern — instruction + defense-in-depth at the tool layer): (1) recovery: `restart_server` / `escalate_to_engineer` check `tool_context.state["recovery_plan_proposed"]` and refuse unless `propose_recovery_plan` was called with a matching `action_type`. (2) intake review (起票前レビュー, added 2026-07-16): `create_support_case` refuses unless `propose_case_draft` registered the draft in `state["case_draft"]` (system_id must match; cleared after filing) — the reporter reviews the case content **before the investigation starts**. Explicit proceed wording in the initial report (「そのまま実施」「以上の内容で」「このまま起票して」「追加の承認・確認は不要」) lets the agent pass either gate in the same turn; one-shot demo prompts in README/Makefile and the eval dataset include such wording wherever a single-turn run is expected.
- **Gemini Cloud Assist MCP** (`app/tools/gcp_assist.py`): the investigation agent gets `investigate_issue` / `ask_cloud_assist` from the remote MCP server `https://geminicloudassist.googleapis.com/mcp` **only when `SANDBOX_PROJECT_ID` is set** (that env var is the investigation-target project, distinct from `GOOGLE_CLOUD_PROJECT`). Unset (eval/CI) → agent is bit-identical to the pre-integration one. Auth = ADC bearer via `header_provider`; `tool_filter` deliberately excludes mutative tools (`invoke_operation`, `design_infra`, `optimize_costs`). Calling the server needs `roles/mcp.toolUser` + `roles/geminicloudassist.user` on the sandbox project — `roles/owner` alone is NOT enough (preview `mcp.tools.call` permission); `scripts/gcp_incident.sh setup` grants both. The GCP incident demo (`store-app` Cloud Run service, broken via `FAIL_MODE=db_timeout`) is managed by `scripts/gcp_incident.sh` / `make gcp-incident-*`.
- **GCE incident scenario** (`store-web-fleet`, see `working/demo-scenarios.md` for ground truth): also gated on `SANDBOX_PROJECT_ID`. Investigation uses `query_incident_bigquery_logs` (`app/tools/gcp_logs.py`) — a parameterized keyword search over the `sre_agents_demo_logs.syslog` BigQuery export (never raw SQL from the LLM). Remediation: `restart_server` performs a **real Compute Engine reset** for systems whose `mock_world` entry has `gce_restart_instance` (store-web-fleet → store-web-02; inventory-mgmt → inventory-mgmt, whose investigation stays fully mock — that VM has no failure injection and exists only as the real restart target, managed by `scripts/inventory_incident.sh` / `make inventory-incident-*`); everything else stays mock. On reset failure it returns `status: error` and does NOT mark the system remediated. Infra is managed by `scripts/gce_incident.sh` / `make gce-incident-*`; note its startup scripts deliberately do NOT auto-start the memleak services (break does), so resolve/reset doesn't re-trigger the incident.

## What this project is

An SRE automation system for a company with sites nationwide (Japan) whose systems run on GCP. Today, when a cloud-side incident affects stores, humans manually take the intake ticket, dig through logs, diagnose, and remediate (e.g. restart a server). That chain is slow. This project replaces/augments it with AI agents.

Two agents are planned (see `SPEC.md` for the full flow):

- **Reception agent (受付エージェント)** — takes the incident report from the store, interviews the reporter until symptoms are sufficiently characterized, writes the case to a database, hands off to the investigation agent.
- **Investigation agent (調査エージェント)** — investigates root cause, consults documentation and prior incident logs, then **presents a recovery plan (root cause / planned action / impact) via `propose_recovery_plan` and waits for the operator's approval (HITL)** before executing `restart_server` or `escalate_to_engineer`. Records the resolution back to the log for future retrieval.

The two agents form a pipeline; the reception agent's DB write is the handoff signal. Recovery execution is gated by an HITL approval step — the operator's initial request may include an explicit pre-approval ("そのまま実施してください" 等) to auto-proceed after the plan is proposed.

## Tech stack (locked by SPEC)

- **Google ADK 2.x** on Python 3 — do not substitute another agent framework.
- **agents-cli** for project scaffolding and skill installation. Use the `google-agents-cli-scaffold` skill to create the project and the `google-agents-cli-workflow` / `google-agents-cli-adk-code` skills for build/code patterns rather than hand-rolling structure.
- **eval** must be runnable — wire up ADK eval from the start (see `google-agents-cli-eval` skill).
- **Makefile** — every commonly-run command (install, run, test, eval, deploy) must have a `make` target. This is a hard requirement from the spec, not a preference.
- Split logic into distinct **Tools** and **sub-agents** rather than one monolithic agent.

## Working notes

- `SPEC.md` is authoritative for scope. When it and any later document disagree, resurface the conflict rather than silently choosing.
- The spec is in Japanese; user-facing agent prompts and any generated Japanese content should stay in Japanese unless the user asks otherwise.
- **Eval must run with `SANDBOX_TOOLS_DISABLED=1`** (`make eval` sets it): agents-cli 0.5.1's `eval generate` builds function declarations from every entry in `tools` and crashes on `McpToolset` ("not a callable object"). The repo `.env` sets `SANDBOX_PROJECT_ID` and ADK loads `.env` with `override=True`, so unsetting the var in the shell does NOT work — only the separate flag does (`app/config.py:get_sandbox_project_id`).
- **Eval uses custom metrics only** (`tests/eval/eval_config.yaml`): the built-in `multi_turn_*` autoraters reject multi-agent traces ("Multiple agents detected"), so do not switch back to them while this app has two agents. Custom LLM-judge templates currently use `{prompt}` because all eval cases are single-turn — if you introduce `agent_data`-continuation cases, drop `{prompt}` from the affected templates (it's not populated for those).
- **agents-cli 0.5.1 `eval generate` does not replay `agent_data` continuation history to the agent** (prior turns end up in the trace but not in the model context). Author eval cases as single-turn prompts; use `agents-cli eval dataset synthesize` for true multi-turn coverage.
- The `agents-cli run` SSE client disconnects after 120s without an event; the Gemini HTTP timeout in `app/sub_agents/__init__.py` (`build_model`) is set below that on purpose — keep it under 120s. Same reason for `sse_read_timeout=110.0` on the Cloud Assist MCP connection in `app/tools/gcp_assist.py` — a long-running `investigate_issue` deep-research call can exceed it, so the instruction tells the agent to keep queries narrow and retry once on timeout.
- `tests/unit/test_agent_wiring.py` builds `root_agent` at import time with ambient env: keep its assertions conditional on `SANDBOX_PROJECT_ID` (function tools are a fixed set; the `McpToolset` is present only when the env var is set), otherwise the suite breaks for anyone who has the var exported.

## agents-cli project guide (generated by scaffold)

### Development Phases

1. **Understand requirements** before writing code.
2. **Build and implement** agent logic in `app/`; test interactively with `agents-cli playground`.
3. **Evaluation loop (main iteration phase)**: start with 1-2 eval cases, `agents-cli eval generate` → `agents-cli eval grade`, iterate (expect 5-10+ rounds). Then `eval compare` (regression diffs), `eval analyze` (failure clustering), `eval optimize` (prompt auto-tune).
4. **Pre-deployment tests**: `uv run pytest tests/unit tests/integration`.
5. **Deploy to dev** — requires explicit human approval; `agents-cli deploy` only after user confirms.
6. **Production**: single-project or full CI/CD via `agents-cli infra cicd` — ask the user.

### Development Commands

| Command | Purpose |
|---------|---------|
| `agents-cli playground` | Interactive local testing |
| `uv run pytest tests/unit tests/integration` | Run unit and integration tests |
| `agents-cli eval dataset synthesize` | Synthesize multi-turn eval scenarios |
| `agents-cli eval generate` / `eval grade` | Produce traces / grade them |
| `agents-cli eval compare` / `eval analyze` / `eval metric list` / `eval optimize` | Regression check / failure clustering / list metrics / prompt tuning |
| `agents-cli lint` | Check code quality |
| `agents-cli infra single-project` | Set up project infrastructure (Terraform) |
| `agents-cli deploy` | Deploy to dev |
| `agents-cli scaffold enhance` / `scaffold upgrade` | Add deployment target / upgrade project |

### Operational Guidelines for Coding Agents

- **Code preservation**: only modify code directly targeted by the request; preserve surrounding code, config values, comments, formatting.
- **NEVER change the model** unless explicitly asked (this repo's pinned choice: `gemini-3.5-flash`, see above).
- **Model 404 errors**: fix `GOOGLE_CLOUD_LOCATION` (e.g. `global`), not the model name.
- **ADK tool imports**: import the tool instance, not the module: `from google.adk.tools.load_web_page import load_web_page`.
- **Run Python with `uv`**: `uv run python script.py`; run `agents-cli install` first.
- **Stop on repeated errors**: same error 3+ times → fix the root cause instead of retrying.
- **Terraform conflicts (409)**: use `terraform import` instead of retrying creation.
