# Q2435: legacy and JSON-RPC envelopes disagree in jsonrpccodec.DecodeJSONRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit the same logical request in the alternate envelope form at the JSON-RPC request body accepted at the public gateway user endpoint so `DecodeJSONRequest` applies weaker validation or a different identity?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `DecodeJSONRequest`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: duplicate/unknown JSON fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `duplicate/unknown JSON fields` in both envelope forms.
- Invariant to test: both envelope forms must converge on identical validation and identity
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: differential test across the two envelope paths
