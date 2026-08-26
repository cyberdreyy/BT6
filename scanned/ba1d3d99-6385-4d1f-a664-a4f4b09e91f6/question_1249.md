# Q1249: secret identifier traversal in http_handler.NewGatewayHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests address another namespace or owner through identifier separators/encoding in the request validated by `NewGatewayHandler` at the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest)?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_handler.go](core/services/gateway/handlers/capabilities/v2/http_handler.go) -> `NewGatewayHandler`
- Entrypoint: the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest)
- Attacker controls: the JSON-RPC method and params (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `JSON-RPC method and params` with separators, encoded delimiters or empty components.
- Invariant to test: identifier components must be validated and joined unambiguously
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test over identifier parsing with hostile components
