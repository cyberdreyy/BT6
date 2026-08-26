# Q4215: origin allowlist bypass in message.SignKS

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests bypass the origin check in `SignKS` at the signed gateway message envelope submitted to the public user endpoint with case, suffix, port or null-origin tricks and drive the gateway from a browser context?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `SignKS`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: field encoding and duplicate JSON keys (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `field encoding and duplicate JSON keys` with crafted Origin values.
- Invariant to test: origin matching must be exact against the configured list
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over isAllowedOrigin with hostile origins
