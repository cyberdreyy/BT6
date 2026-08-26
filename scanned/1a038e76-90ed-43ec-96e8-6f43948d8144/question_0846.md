# Q0846: connection identity rebinding in wsconnection.NewWSConnectionWrapper

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests open a connection through `NewWSConnectionWrapper` at an established gateway WebSocket connection and then act under an identity established by another connection because the registry is keyed loosely?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `NewWSConnectionWrapper`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: connection reset/reconnect timing (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Open concurrent connections with `connection reset/reconnect timing` claiming the same identity.
- Invariant to test: each connection must carry its own verified identity for its lifetime
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: concurrency test asserting per-connection identity isolation
