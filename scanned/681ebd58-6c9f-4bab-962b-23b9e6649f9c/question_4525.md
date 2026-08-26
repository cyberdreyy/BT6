# Q4525: error path echoes internal state in gateway.NewGateway

## Question
Do gateway errors produced near `NewGateway` at ProcessRequest on the public gateway user endpoint disclose node addresses, DON membership, internal URLs or key identifiers to any internet client with an arbitrary externally-owned key sending signed gateway requests?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `NewGateway`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: the message payload (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `message payload`.
- Invariant to test: gateway errors must be generic
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting error payloads match an allowlist
