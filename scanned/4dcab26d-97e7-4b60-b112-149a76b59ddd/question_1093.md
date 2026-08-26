# Q1093: request id mismatch tolerated in http_handler.NewGatewayHandler

## Question
Does `NewGatewayHandler` at the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest) tolerate a mismatch between the id inside a signed payload and the id of the request being answered, letting any internet client with an arbitrary externally-owned key sending signed gateway requests splice a response from another request?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_handler.go](core/services/gateway/handlers/capabilities/v2/http_handler.go) -> `NewGatewayHandler`
- Entrypoint: the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest)
- Attacker controls: the outgoing request URL, headers and body (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `outgoing request URL, headers and body` whose ids differ.
- Invariant to test: the signed id must equal the served request id
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting mismatched ids are rejected
