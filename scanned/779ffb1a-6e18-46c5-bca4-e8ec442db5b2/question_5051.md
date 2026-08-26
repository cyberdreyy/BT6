# Q5051: sender field trusted over recovered signer in jsonrpccodec.DecodeLegacyResponse

## Question
Does code reached from `DecodeLegacyResponse` at the JSON-RPC request body accepted at the public gateway user endpoint use the self-declared sender field rather than the address recovered from the signature, letting any internet client with an arbitrary externally-owned key sending signed gateway requests impersonate another gateway user?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `DecodeLegacyResponse`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: duplicate/unknown JSON fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `duplicate/unknown JSON fields` with a sender field naming a victim and a valid attacker signature.
- Invariant to test: the acting identity must be the recovered signer only
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: unit test asserting sender is overwritten by the recovered address
