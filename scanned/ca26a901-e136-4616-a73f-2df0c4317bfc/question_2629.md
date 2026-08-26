# Q2629: connection identity rebinding in gateway.setupFromNewConfig

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests open a connection through `setupFromNewConfig` at ProcessRequest on the public gateway user endpoint and then act under an identity established by another connection because the registry is keyed loosely?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `setupFromNewConfig`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: the message payload (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Open concurrent connections with `message payload` claiming the same identity.
- Invariant to test: each connection must carry its own verified identity for its lifetime
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: concurrency test asserting per-connection identity isolation
