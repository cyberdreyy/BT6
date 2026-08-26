# Q5900: error path echoes internal state in message.ExtractSigner

## Question
Do gateway errors produced near `ExtractSigner` at the signed gateway message envelope submitted to the public user endpoint disclose node addresses, DON membership, internal URLs or key identifiers to any internet client with an arbitrary externally-owned key sending signed gateway requests?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `ExtractSigner`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: the signature bytes (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `signature bytes`.
- Invariant to test: gateway errors must be generic
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting error payloads match an allowlist
