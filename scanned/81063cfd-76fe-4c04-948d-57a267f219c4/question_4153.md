# Q4153: connection identity rebinding in jsonrpccodec.EncodeLegacyRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests open a connection through `EncodeLegacyRequest` at the JSON-RPC request body accepted at the public gateway user endpoint and then act under an identity established by another connection because the registry is keyed loosely?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `EncodeLegacyRequest`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: duplicate/unknown JSON fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Open concurrent connections with `duplicate/unknown JSON fields` claiming the same identity.
- Invariant to test: each connection must carry its own verified identity for its lifetime
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: concurrency test asserting per-connection identity isolation
