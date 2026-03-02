# Release Checklist (Phase 6)

## Pre-release
- [ ] CI green on `main` (`.github/workflows/ci.yml`)
- [ ] Security scan passes (`python scripts/secret_scan.py`)
- [ ] `pawbot doctor` reviewed (critical issues resolved)
- [ ] `pawbot metrics` reviewed (SLO trend acceptable)
- [ ] Changelog updated

## Staging validation
- [ ] Copy `configs/staging.config.template.json` to staging config
- [ ] Set real provider keys in staging only
- [ ] Start gateway in staging and run smoke tests:
  - [ ] `pawbot --help`
  - [ ] `pawbot status`
  - [ ] `pawbot skills list`
  - [ ] one inbound/outbound channel test

## Production release
- [ ] Tag release: `git tag vX.Y.Z`
- [ ] Push tag: `git push origin vX.Y.Z`
- [ ] Deploy package/artifacts
- [ ] Verify dashboard `/api/health` and `/api/observability`

## Post-release
- [ ] Monitor logs for 30–60 min
- [ ] Confirm no dead-letter growth spike
- [ ] Confirm latency p95 remains within target
