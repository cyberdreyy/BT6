# Q3906: revoked workflow still executable in handler.Callback

## Question
Does a workflow deleted, paused or de-authorized upstream remain executable through `Callback` at the gateway handler interface boundary every public user request passes through until a cache expires, letting any internet client with an arbitrary externally-owned key sending signed gateway requests keep triggering it?

## Target
- File/function: [core/services/gateway/handlers/handler.go](core/services/gateway/handlers/handler.go) -> `Callback`
- Entrypoint: the gateway handler interface boundary every public user request passes through
- Attacker controls: the method and payload of the user request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `method and payload of the user request` after revocation.
- Invariant to test: revocation must take effect before the next accepted trigger
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: integration test triggering after revocation
