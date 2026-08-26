# Q4285: method routing selects a weaker handler in http_handler.HandleNodeMessage

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests name a method at the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest) that `HandleNodeMessage` routes to a handler with weaker authorization while the payload targets a privileged capability?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_handler.go](core/services/gateway/handlers/capabilities/v2/http_handler.go) -> `HandleNodeMessage`
- Entrypoint: the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest)
- Attacker controls: the JSON-RPC method and params (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `JSON-RPC method and params` with a mismatched method/payload pair.
- Invariant to test: method routing and payload authorization must be consistent
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: matrix test over method/payload mismatches
