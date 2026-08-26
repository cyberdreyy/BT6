# Q1792: revoked workflow still executable in handler.UserCallbackPayload

## Question
Does a workflow deleted, paused or de-authorized upstream remain executable through `UserCallbackPayload` at the gateway handler interface boundary every public user request passes through until a cache expires, letting any internet client with an arbitrary externally-owned key sending signed gateway requests keep triggering it?

## Target
- File/function: [core/services/gateway/handlers/handler.go](core/services/gateway/handlers/handler.go) -> `UserCallbackPayload`
- Entrypoint: the gateway handler interface boundary every public user request passes through
- Attacker controls: callback correlation fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `callback correlation fields` after revocation.
- Invariant to test: revocation must take effect before the next accepted trigger
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: integration test triggering after revocation
