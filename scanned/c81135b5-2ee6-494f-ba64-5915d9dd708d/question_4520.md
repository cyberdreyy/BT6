# Q4520: error path echoes internal state in wsserver.handleRequest

## Question
Do gateway errors produced near `handleRequest` at the public gateway WebSocket endpoint and its auth handshake disclose node addresses, DON membership, internal URLs or key identifiers to any internet client with an arbitrary externally-owned key sending signed gateway requests?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `handleRequest`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: the auth header presented at handshake (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `auth header presented at handshake`.
- Invariant to test: gateway errors must be generic
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting error payloads match an allowlist
