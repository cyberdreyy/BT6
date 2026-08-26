# Q3140: unauthenticated method reachable in jsonrpccodec.DecodeJSONRequest

## Question
Is a gateway method routed by `DecodeJSONRequest` at the JSON-RPC request body accepted at the public gateway user endpoint reachable without the authorization the handler assumes, letting any internet client with an arbitrary externally-owned key sending signed gateway requests invoke privileged capability paths?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `DecodeJSONRequest`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: method, id and params JSON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `method, id and params JSON` for each advertised method without credentials.
- Invariant to test: every method must declare and enforce its own authorization
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: matrix test invoking every method unauthenticated
