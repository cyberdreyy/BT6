# Q0844: connection identity rebinding in httpserver.ensureLimiters

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests open a connection through `ensureLimiters` at the public gateway user HTTP endpoint (POST to the configured user path) and then act under an identity established by another connection because the registry is keyed loosely?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `ensureLimiters`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: source address and forwarding headers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Open concurrent connections with `source address and forwarding headers` claiming the same identity.
- Invariant to test: each connection must carry its own verified identity for its lifetime
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: concurrency test asserting per-connection identity isolation
