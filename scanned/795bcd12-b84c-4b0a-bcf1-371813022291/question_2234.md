# Q2234: method routing selects a weaker handler in handler.UserCallbackPayload

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests name a method at the gateway handler interface boundary every public user request passes through that `UserCallbackPayload` routes to a handler with weaker authorization while the payload targets a privileged capability?

## Target
- File/function: [core/services/gateway/handlers/handler.go](core/services/gateway/handlers/handler.go) -> `UserCallbackPayload`
- Entrypoint: the gateway handler interface boundary every public user request passes through
- Attacker controls: callback correlation fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `callback correlation fields` with a mismatched method/payload pair.
- Invariant to test: method routing and payload authorization must be consistent
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: matrix test over method/payload mismatches
