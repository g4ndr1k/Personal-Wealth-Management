# Mail Agent Safety Matrix

Phase 4E/4F release stabilization reference. This matrix summarizes the safety-critical Mail Agent surfaces as implemented in the current release.

Current safety model:

- AI drafts only.
- Deterministic validation checks every draft.
- Human Save Rule is required before rule rows are created.
- Draft endpoint writes audit only.
- Golden probe writes quality run only.
- Explain endpoint is read-only.
- Save Rule is the only Rule AI path that creates rule rows.
- Draft and probe endpoints write observability rows only.
- Explanation is read-only.
- Approval execution is mock-only / final-verification-only in this phase.
- Gmail/IMAP mutation remains disabled/deferred.
- No iMessage is sent at draft, probe, explain, or approval mock-execution time.
- No bridge call is made from draft, probe, or explain.
- Cloud LLM integration remains deferred; Rule AI is local-first and disabled by default.

| Feature / Endpoint | Phase | Writes rule rows? | Writes audit/quality rows? | Sends iMessage? | Calls bridge? | Calls Gmail/IMAP? | Can mutate mailbox? | Requires human save/approval? | Default enabled? | Relevant tests |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|
| `POST /api/mail/rules/ai/draft` | 4F.1a-4F.1g | No | Yes, draft audit only | No | No | No | No | Human Save Rule required to persist | Rule AI disabled by default | `agent/tests/test_rule_ai_builder.py`, `agent/tests/test_rule_ai_audit.py`, `mail-dashboard` `test:rules-ui`, `test:e2e` |
| `POST /api/mail/rules/ai/golden-probe` | 4F.1e-4F.1g | No | Yes, quality run only | No | No | No | No | No rule save path | Rule AI disabled by default; endpoint returns disabled safely | `agent/tests/test_rule_ai_golden_probe.py`, `mail-dashboard` `test:e2e` |
| `GET /api/mail/rules/ai/audit/summary` | 4F.1g | No | No new writes; reads audit metrics | No | No | No | No | No | Yes, read-only API | `agent/tests/test_rule_ai_audit.py`, `mail-dashboard` `test:e2e` |
| `GET /api/mail/rules/ai/audit/recent` | 4F.1g | No | No new writes; reads audit rows | No | No | No | No | No | Yes, read-only API | `agent/tests/test_rule_ai_audit.py`, `mail-dashboard` `test:e2e` |
| `GET /api/mail/rules/ai/golden-probe/runs` | 4F.1g | No | No new writes; reads quality rows | No | No | No | No | No | Yes, read-only API | `agent/tests/test_rule_ai_audit.py`, `mail-dashboard` `test:e2e` |
| `POST /api/mail/rules` | 4A / 4F human save path | Yes | Rule audit/event behavior only as implemented by save path | No | No | No | No | Yes, explicit human Save Rule | Yes | `agent/tests/test_rule_ai_save_compatibility.py`, `agent/tests/test_rule_ai_builder.py`, `mail-dashboard` `test:rules-ui`, `test:e2e` |
| `POST /api/mail/rules/explain` | 4F.2a | No | No | No | No | No | No | No | Yes, read-only API | `agent/tests/test_rule_explain.py`, `mail-dashboard` `test:e2e` |
| Control Center approve/reject | 4D.1-4F.2c | No | Yes, approval decision audit | No | No | No | No | Yes, explicit approve/reject | Approval layer enabled for operator workflow | `agent/tests/test_action_approvals.py`, `mail-dashboard` `test:e2e` |
| Control Center execute / Mock verify + audit | 4E.1-4E.2 | No | Yes, execution/event audit | No | No bridge iMessage call | Read-only verification only when configured and identity is present | No; mock executor only | Yes, explicit approval plus explicit execute | Mock/final verification path only | `agent/tests/test_action_execution.py`, `agent/tests/test_action_verification.py`, `mail-dashboard` `test:e2e` |
| Approval cleanup preview | 4D.4 | No | No | No | No | No | No | No | Yes, read-only preview | `agent/tests/test_action_approvals.py`, `mail-dashboard` `test:e2e` |
| Approval cleanup | 4D.4 | No | Yes, lifecycle/audit updates for expired/archive cleanup | No | No | No | No | Yes, explicit cleanup request; force required when disabled | Disabled by default | `agent/tests/test_action_approvals.py`, `mail-dashboard` `test:e2e` |
| Rule AI Golden Probe CLI | 4F.1d | No | No direct DB write; calls draft endpoint only when operator runs it | No | No | No | No | Manual operator command | Not automatic | `agent/tests/test_rule_ai_golden_probe.py` |
| Preflight | 4F.1g / 4F.2d | No | No runtime DB writes; writes markdown report file | No | Health/readiness probes only; no draft/probe/explain bridge calls | No Gmail/IMAP mutation | No | Manual operator command | Manual | `scripts/mailagent_verify_phase4.sh` |
| CI static preflight `scripts/mailagent_preflight.py --ci` | 4F.2e | No | No | No | No | No | No | No | GitHub Actions / manual CI check | `.github/workflows/mail-agent-phase4.yml` |
| GitHub Actions Phase 4 workflow | 4F.2e | No production writes; tests use temp DBs | Test-local only | No | No | No | No | No | Pull request, `main`, weekly schedule, manual dispatch | `.github/workflows/mail-agent-phase4.yml` |

## Explicit Safety Notes

- The draft endpoint writes `mail_rule_ai_draft_audit` only; it does not write `mail_rules`, `mail_rule_conditions`, or `mail_rule_actions`.
- The golden probe endpoint writes `mail_rule_ai_golden_probe_runs` only; it does not write rule rows.
- The explanation endpoint is read-only and uses deterministic preview logic only.
- Save Rule (`POST /api/mail/rules`) is the only Rule AI workflow path that creates rule rows, and it is a human action.
- Approval execution remains mock-only / final-verification-only at this phase. Final verification may read mailbox identity through the read-only adapter, then mock execution writes audit metadata only.
- Gmail/IMAP mutation remains disabled/deferred. No `STORE`, `MOVE`, `COPY`, `CREATE`, `EXPUNGE`, Gmail label mutation, Gmail Spam move, reply, forward, delete, unsubscribe, or webhook execution is added by Phase 4F.2d.
- Playwright safety tests mock all `/api/mail/*` routes and fail closed on unmocked mail API calls.
- CI uses fake env vars only, does not require secrets, does not start Docker or local Mac services, and does not call real Ollama, Gmail/IMAP, iMessage, bridge, or NAS mounts.
- CI static preflight checks committed safety defaults and static markers only. Local preflight remains the operator environment inventory and may report non-fatal local warnings.
