# Q4160: undecodable responses counted as valid in http_handler.HandleNodeMessage

## Question
Does `HandleNodeMessage` at the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest) count undecodable or error responses toward success, letting any internet client with an arbitrary externally-owned key sending signed gateway requests force a result with fewer honest contributions?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_handler.go](core/services/gateway/handlers/capabilities/v2/http_handler.go) -> `HandleNodeMessage`
- Entrypoint: the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest)
- Attacker controls: the outgoing request URL, headers and body (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger the mixed-response branch with `outgoing request URL, headers and body`.
- Invariant to test: only successfully decoded, verified responses may count
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test with mixed decodable/undecodable responses
