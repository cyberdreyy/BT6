# Q1009: path/URL split confusion in multihandler.NewMultiHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests craft a request path at gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests that `NewMultiHandler` splits differently from the routing layer, reaching a handler or DON that was not authorized?

## Target
- File/function: [core/services/gateway/multihandler.go](core/services/gateway/multihandler.go) -> `NewMultiHandler`
- Entrypoint: gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests
- Attacker controls: the requested method name (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `requested method name` with extra segments, encoded slashes or empty segments.
- Invariant to test: splitting and routing must agree on the same canonical path
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over splitURL with hostile paths
