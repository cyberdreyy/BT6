# Q1630: timestamp/nonce window too wide in jsonrpccodec.DecodeRawRequest

## Question
Does the freshness check near `DecodeRawRequest` at the JSON-RPC request body accepted at the public gateway user endpoint accept a wide or unbounded window, letting any internet client with an arbitrary externally-owned key sending signed gateway requests replay captured requests long after capture?

## Target
- File/function: [core/services/gateway/api/jsonrpccodec.go](core/services/gateway/api/jsonrpccodec.go) -> `DecodeRawRequest`
- Entrypoint: the JSON-RPC request body accepted at the public gateway user endpoint
- Attacker controls: nesting, type confusion and encoding of params (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `nesting, type confusion and encoding of params` with old/future timestamps.
- Invariant to test: freshness must be bounded and monotonic per sender
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test at the window boundaries
