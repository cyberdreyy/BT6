# Q1639: retry amplification per request in http_handler.NewGatewayHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests make one accepted request at the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest) cause repeated node work through the retry logic near `NewGatewayHandler`, multiplying DON execution per unit of entitlement?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_handler.go](core/services/gateway/handlers/capabilities/v2/http_handler.go) -> `NewGatewayHandler`
- Entrypoint: the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest)
- Attacker controls: response routing identifiers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `response routing identifiers` that never reaches a terminal state.
- Invariant to test: retries must be bounded per request and counted against the caller's quota
- Expected Immunefi impact: High - rate limit violation: unpaid/unauthorized DON execution beyond the caller's entitlement
- Fast validation: test counting node messages produced by one user request
