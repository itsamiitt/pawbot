# Rollback Runbook (Phase 6)

## Trigger conditions
- Error rate spike after release
- Channel delivery failure spike
- Critical regression in command execution

## Fast rollback
1. Identify last known good tag (example `v1.2.3`)
2. Roll back deployment to that tag/commit
3. Restart pawbot services
4. Verify health:
   - `pawbot status`
   - `pawbot doctor`
   - dashboard `/api/health`

## Data/config safety
- Never overwrite production config without backup
- Keep `~/.pawbot/config.json.bak` before release changes
- If needed, restore previous config and restart

## Verification after rollback
- Confirm inbound messages process normally
- Confirm outbound delivery works and dead-letter queue is stable
- Confirm SLO success rate recovers

## Incident follow-up
- Capture root cause
- Add regression test
- Update checklist to prevent recurrence
