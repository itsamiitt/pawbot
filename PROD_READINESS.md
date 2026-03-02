# Pawbot Production Readiness Plan

This plan is focused on making Pawbot stable, secure, and observable in real production usage.

## Phase 1 — Config & Startup Hardening (Day 1-2)

### Goals
- Remove config ambiguity
- Fail fast on bad startup states

### Tasks
- [ ] Standardize one canonical key format in config docs (`snake_case`)
- [ ] Keep compatibility mapping for legacy keys (e.g. `skillsDir -> skills_dir`)
- [ ] Add startup validation for:
  - [ ] workspace path exists / writable
  - [ ] skills path exists
  - [ ] provider credentials present for selected model/provider
  - [ ] enabled channels have required tokens
- [ ] Make `pawbot doctor` return non-zero exit on critical failures

### Exit Criteria
- `pawbot doctor` catches all config/path/token issues before runtime

---

## Phase 2 — Skills & Agent Reliability (Day 3-5)

### Goals
- Ensure skills are always discoverable and runnable
- Prevent runtime regressions

### Tasks
- [x] Fix skills path resolution for both `skills_dir` and `skillsDir`
- [x] Point local config skills path to `~/.pawbot/workspace/skills`
- [ ] Add tests for skills loading sources:
  - [ ] runtime json skills
  - [ ] workspace markdown skills
  - [ ] builtin skills
- [ ] Add regression test for key alias mismatch
- [ ] Add `pawbot skills doctor` command (optional) to verify all skill paths

### Exit Criteria
- `pawbot skills list` and skill execution work in clean and existing environments

---

## Phase 3 — Channel & Messaging Stability (Week 2)

### Goals
- Reliable inbound/outbound messaging with retries and observability

### Tasks
- [ ] Add idempotency key handling for outbound sends
- [ ] Add retry/backoff strategy for network/channel failures
- [ ] Add dead-letter handling for failed sends
- [ ] Add smoke tests for enabled channels (Telegram first)
- [ ] Add channel health status in `pawbot status`

### Exit Criteria
- Message success rate > 99% in staged testing

---

## Phase 4 — Security & Safety Controls (Week 2)

### Goals
- Safe-by-default production behavior

### Tasks
- [ ] Enforce confirmation flow for destructive actions
- [ ] Restrict dangerous tools by profile/environment
- [ ] Add secret scanning pre-commit/CI check
- [ ] Add audit event for destructive tool usage
- [ ] Ensure no hardcoded credentials in repo/config examples

### Exit Criteria
- Security checks pass in CI and runtime audit logs are complete

---

## Phase 5 — Observability, SLOs, and Operations (Week 3)

### Goals
- Full visibility + clear operational targets

### Tasks
- [ ] Add structured logging with session/request IDs
- [ ] Add error budget metrics (tool failures, provider failures, channel failures)
- [ ] Define SLOs:
  - [ ] command success rate
  - [ ] response latency p50/p95
  - [ ] channel delivery success
- [ ] Add dashboard panel for health + failures
- [ ] Add backup/restore runbook for `~/.pawbot`

### Exit Criteria
- Team can detect, debug, and recover from failures quickly

---

## Phase 6 — Release Process & CI Gate (Week 3)

### Goals
- Ship changes safely and predictably

### Tasks
- [ ] CI gate: lint + tests + type checks + smoke checks
- [ ] Staging environment with production-like config
- [ ] Release checklist with rollback steps
- [ ] Changelog per release
- [ ] Tag stable releases

### Exit Criteria
- Every release is repeatable, test-backed, and rollback-capable

---

## Immediate Next Actions (Now)

1. [ ] Add tests for `SkillWriter` config alias handling
2. [ ] Add startup config validation test cases
3. [ ] Add `doctor` critical-fail behavior test
4. [ ] Add channel health summary to `pawbot status`
5. [ ] Create a lightweight staging config template
