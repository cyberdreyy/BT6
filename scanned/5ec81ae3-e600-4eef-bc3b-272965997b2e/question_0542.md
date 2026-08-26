# Q0542: cached response served to a different requester in http_handler.NewGatewayHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests obtain a cached response produced for another user because the cache key computed near `NewGatewayHandler` at the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest) omits the sender or authorization context?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_handler.go](core/services/gateway/handlers/capabilities/v2/http_handler.go) -> `NewGatewayHandler`
- Entrypoint: the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest)
- Attacker controls: the JSON-RPC method and params (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Repeat `JSON-RPC method and params` with the victim's request fields.
- Invariant to test: cache keys must include the authenticated sender and authorization inputs
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting cache isolation across senders
