# PHASE 17 — Production Hardening Execution Board

Use this file as the active tracker while implementing `../PROD_READINESS.md`.

## Status Legend
- TODO
- IN_PROGRESS
- BLOCKED
- DONE

## Work Board

### 17.1 Config Validation`n- Status: DONE
- Owner: core
- Deliverables:`n  - startup validation module`n  - failing `doctor` for critical issues`n- Completed:`n  - Added `pawbot/config/validation.py` runtime checks`n  - Gateway now blocks startup on critical config/runtime issues`n  - `pawbot doctor` exits non-zero when critical checks fail

### 17.2 Skills Reliability`n- Status: DONE
- Owner: core
- Completed:
  - skills path alias handling fixed (`skills_dir` + `skillsDir`)
  - config path aligned to `~/.pawbot/workspace/skills`
- Remaining:`n  - optional `skills doctor` command (deferred)

### 17.3 Messaging Reliability`n- Status: IN_PROGRESS
- Owner: channels
- Deliverables:`n  - retry/backoff`n  - idempotency`n  - dead-letter queue`n- Completed:`n  - Added outbound retry with exponential backoff in ChannelManager`n  - Added outbound idempotency dedupe keys`n  - Added dead-letter JSONL logging for failed/unknown-channel messages`n  - Added automated tests in tests/test_channel_manager_reliability.py

### 17.4 Security Controls`n- Status: IN_PROGRESS
- Owner: security
- Deliverables:`n  - dangerous action guardrail tests`n  - audit completeness`n- Completed:`n  - Added production command restrictions in ExecTool (PAWBOT_ENV=production)`n  - Added audit completeness (`caller`) in security audit events`n  - Added local+CI secret scanning (`scripts/secret_scan.py`, pre-commit, GitHub workflow)`n  - Removed token-like examples from installation docs

### 17.5 Observability`n- Status: IN_PROGRESS
- Owner: platform
- Deliverables:`n  - request/session correlated logs`n  - health dashboard panels`n  - SLO metrics`n- Completed:`n  - Added trace-file SLO summarization helpers in telemetry (`summarize_spans`, `summarize_trace_file`)`n  - Added SLO display in `pawbot metrics` CLI command`n  - Added dashboard observability endpoint `/api/observability``n  - Added observability tests for SLO summaries

### 17.6 Release Pipeline`n- Status: IN_PROGRESS
- Owner: platform
- Deliverables:`n  - CI quality gate`n  - staging + rollback runbook`n- Completed:`n  - Added CI workflow (`.github/workflows/ci.yml`) with compile/tests/smoke/secret scan`n  - Added staging config template (`configs/staging.config.template.json`)`n  - Added release checklist (`runbooks/RELEASE_CHECKLIST.md`)`n  - Added rollback runbook (`runbooks/ROLLBACK_RUNBOOK.md`)





