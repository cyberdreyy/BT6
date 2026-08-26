# Q0305: length/format validation gap in http_handler.NewGatewayHandler

## Question
Does the field validation in `NewGatewayHandler` at the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest) accept an over-long, truncated or non-hex identifier that later code slices or parses unchecked, letting any internet client with an arbitrary externally-owned key sending signed gateway requests address a different workflow?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_handler.go](core/services/gateway/handlers/capabilities/v2/http_handler.go) -> `NewGatewayHandler`
- Entrypoint: the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest)
- Attacker controls: the JSON-RPC method and params (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `JSON-RPC method and params` at and beyond the documented length bounds.
- Invariant to test: every identifier must be length- and charset-validated before use
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test at length boundaries for each identifier field
