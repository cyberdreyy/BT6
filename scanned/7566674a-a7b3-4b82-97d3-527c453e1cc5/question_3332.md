# Q3332: hex/address normalization differences in jsonrpccodec.DecodeJSONRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present an address or identifier at the JSON-RPC request body accepted at the public gateway user endpoint in a casing/encoding variant that `DecodeJSONRequest` treats as distinct from the allowlisted form, bypassing an authorization or quota keyed on the other form?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `DecodeJSONRequest`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: method, id and params JSON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `method, id and params JSON` in mixed case, checksummed, zero-padded or 0x-less form.
- Invariant to test: identifiers must be canonicalized once before any authorization or accounting decision
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: table test over normalization for all encodings of one identity
