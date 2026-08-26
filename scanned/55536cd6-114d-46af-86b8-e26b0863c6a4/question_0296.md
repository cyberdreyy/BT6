# Q0296: replay across time, don or method in jsonrpccodec.DecodeRawRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests capture a signed request at the JSON-RPC request body accepted at the public gateway user endpoint and replay it through `DecodeRawRequest` for another DON, method or later time because no nonce/expiry binds it?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `DecodeRawRequest`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: method, id and params JSON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `method, id and params JSON` against other donIds/methods.
- Invariant to test: each signed request must be single-use and bound to don, method and a validity window
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a captured message and asserting rejection
