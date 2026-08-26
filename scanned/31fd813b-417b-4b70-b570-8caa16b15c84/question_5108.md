# Q5108: signature malleability accepted in jsonrpccodec.DecodeLegacyResponse

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present a malleable or alternative-encoding signature at the JSON-RPC request body accepted at the public gateway user endpoint that `DecodeLegacyResponse` accepts, producing a second valid form of an existing request (replay under a new id)?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `DecodeLegacyResponse`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: nesting, type confusion and encoding of params (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `nesting, type confusion and encoding of params` with high-S/alternate v/padded r-s values.
- Invariant to test: signature encoding must be canonical and single-valued
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over ExtractSigner with malleable signatures
