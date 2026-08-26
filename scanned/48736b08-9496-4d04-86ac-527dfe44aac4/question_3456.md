# Q3456: empty or absent signature accepted in wsserver.handleHealthCheck

## Question
Does a request with an empty, zero or absent signature at the public gateway WebSocket endpoint and its auth handshake pass through `handleHealthCheck` and receive an identity (zero address) that later checks treat as valid?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `handleHealthCheck`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: claimed node/user address (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `claimed node/user address` without signature material.
- Invariant to test: missing signatures must be rejected before identity assignment
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test with empty/zero signatures
