# Q0621: cache poisoning of another user's result in http_handler.NewGatewayHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests write into the cache consulted by `NewGatewayHandler` at the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest) so a later legitimate request receives attacker-controlled data used by a workflow?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_handler.go](core/services/gateway/handlers/capabilities/v2/http_handler.go) -> `NewGatewayHandler`
- Entrypoint: the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest)
- Attacker controls: the outgoing request URL, headers and body (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Prime the cache with `outgoing request URL, headers and body`.
- Invariant to test: only DON-verified responses may populate the cache, keyed to their request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test priming and then asserting the victim's response origin
