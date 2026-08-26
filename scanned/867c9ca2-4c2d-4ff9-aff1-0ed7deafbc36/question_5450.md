# Q5450: handshake identity not verified in jsonrpccodec.DecodeLegacyResponse

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests complete the auth handshake around `DecodeLegacyResponse` at the JSON-RPC request body accepted at the public gateway user endpoint while claiming an address they do not control, joining as a privileged participant?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `DecodeLegacyResponse`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: nesting, type confusion and encoding of params (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `nesting, type confusion and encoding of params` with a mismatched claimed address and signature.
- Invariant to test: the handshake must bind the claimed address to a signature over a server challenge
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over the handshake with mismatched address/signature
