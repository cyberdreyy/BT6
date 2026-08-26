# Q1861: empty or absent signature accepted in wsconnection.NewWSConnectionWrapper

## Question
Does a request with an empty, zero or absent signature at an established gateway WebSocket connection pass through `NewWSConnectionWrapper` and receive an identity (zero address) that later checks treat as valid?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `NewWSConnectionWrapper`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: concurrent connections claiming the same identity (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `concurrent connections claiming the same identity` without signature material.
- Invariant to test: missing signatures must be rejected before identity assignment
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test with empty/zero signatures
