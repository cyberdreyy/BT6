# Q4524: error path echoes internal state in jsonrpccodec.EncodeLegacyRequest

## Question
Do gateway errors produced near `EncodeLegacyRequest` at the JSON-RPC request body accepted at the public gateway user endpoint disclose node addresses, DON membership, internal URLs or key identifiers to any internet client with an arbitrary externally-owned key sending signed gateway requests?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `EncodeLegacyRequest`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: duplicate/unknown JSON fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `duplicate/unknown JSON fields`.
- Invariant to test: gateway errors must be generic
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting error payloads match an allowlist
