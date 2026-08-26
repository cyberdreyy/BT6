# Q1938: signature not covering all authorization fields in jsonrpccodec.DecodeJSONRequest

## Question
Does the signature validated on the path through `DecodeJSONRequest` at the JSON-RPC request body accepted at the public gateway user endpoint cover every field used for authorization (sender, method, donId, receiver, payload), or can any internet client with an arbitrary externally-owned key sending signed gateway requests mutate an uncovered field after signing?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `DecodeJSONRequest`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: method, id and params JSON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Sign one message, then alter `method, id and params JSON` before sending.
- Invariant to test: the signed digest must commit to every field later used for routing or authorization
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test mutating each field of a signed message and asserting rejection
