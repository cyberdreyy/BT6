# Q1558: per-owner quota not enforced in handler.UserCallbackPayload

## Question
Does `UserCallbackPayload` at the gateway handler interface boundary every public user request passes through enforce rate/quota per authenticated owner, or can any internet client with an arbitrary externally-owned key sending signed gateway requests rotate identifiers to obtain unlimited DON execution charged elsewhere?

## Target
- File/function: [core/services/gateway/handlers/handler.go](core/services/gateway/handlers/handler.go) -> `UserCallbackPayload`
- Entrypoint: the gateway handler interface boundary every public user request passes through
- Attacker controls: callback correlation fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Rotate `callback correlation fields` across submissions.
- Invariant to test: quotas must key on the verified owner and be enforced before dispatch
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test rotating identifiers and asserting the quota still applies
