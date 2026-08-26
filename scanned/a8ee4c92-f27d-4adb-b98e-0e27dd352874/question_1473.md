# Q1473: unauthenticated method reachable in message.Validate

## Question
Is a gateway method routed by `Validate` at the signed gateway message envelope submitted to the public user endpoint reachable without the authorization the handler assumes, letting any internet client with an arbitrary externally-owned key sending signed gateway requests invoke privileged capability paths?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `Validate`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: every MessageBody field (sender, method, donId, messageId, payload) (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `every MessageBody field (sender, method, donId, messageId, payload)` for each advertised method without credentials.
- Invariant to test: every method must declare and enforce its own authorization
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: matrix test invoking every method unauthenticated
