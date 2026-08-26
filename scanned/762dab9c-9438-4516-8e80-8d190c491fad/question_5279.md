# Q5279: length/alignment helper mismatch in jsonrpccodec.DecodeLegacyResponse

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a string/byte field at the JSON-RPC request body accepted at the public gateway user endpoint whose alignment or length handling in `DecodeLegacyResponse` makes the parsed value differ from the signed value?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `DecodeLegacyResponse`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: nesting, type confusion and encoding of params (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `nesting, type confusion and encoding of params` with padding, embedded NULs or non-UTF8 bytes.
- Invariant to test: encode/decode must round-trip exactly for every input
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: round-trip fuzz-style table test over the alignment helpers
