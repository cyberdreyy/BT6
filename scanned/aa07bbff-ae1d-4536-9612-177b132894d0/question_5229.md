# Q5229: signature validation optional by method in http_handler.send

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests select a method at the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest) for which `send` skips signed-response validation, receiving an unverified or attacker-influenceable result?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_handler.go](core/services/gateway/handlers/capabilities/v2/http_handler.go) -> `send`
- Entrypoint: the v2 gateway HTTP handler (HandleJSONRPCUserMessage/makeOutgoingRequest)
- Attacker controls: the outgoing request URL, headers and body (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `outgoing request URL, headers and body` on each advertised method.
- Invariant to test: signed validation must apply to every method returning sensitive or trusted data
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: matrix test asserting validation runs for every method
