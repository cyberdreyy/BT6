# Q4156: connection identity rebinding in connectionmanager.DONConnectionManager

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests open a connection through `DONConnectionManager` at the gateway node-facing handshake and connection registry as observed from a user request and then act under an identity established by another connection because the registry is keyed loosely?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `DONConnectionManager`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: handshake timing and repetition (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Open concurrent connections with `handshake timing and repetition` claiming the same identity.
- Invariant to test: each connection must carry its own verified identity for its lifetime
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: concurrency test asserting per-connection identity isolation
