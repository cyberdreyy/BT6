# Q4937: empty or absent signature accepted in jsonrpccodec.EncodeLegacyRequest

## Question
Does a request with an empty, zero or absent signature at the JSON-RPC request body accepted at the public gateway user endpoint pass through `EncodeLegacyRequest` and receive an identity (zero address) that later checks treat as valid?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `EncodeLegacyRequest`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: nesting, type confusion and encoding of params (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `nesting, type confusion and encoding of params` without signature material.
- Invariant to test: missing signatures must be rejected before identity assignment
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test with empty/zero signatures
