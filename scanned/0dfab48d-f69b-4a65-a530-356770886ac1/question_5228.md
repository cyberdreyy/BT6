# Q5228: signature validation optional by method in handler.handleWebAPITriggerMessage

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests select a method at web-API trigger and outgoing-request handling on the public gateway user endpoint for which `handleWebAPITriggerMessage` skips signed-response validation, receiving an unverified or attacker-influenceable result?

## Target
- File/function: [core/services/gateway/handlers/capabilities/handler.go](core/services/gateway/handlers/capabilities/handler.go) -> `handleWebAPITriggerMessage`
- Entrypoint: web-API trigger and outgoing-request handling on the public gateway user endpoint
- Attacker controls: the trigger payload and workflow selector (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `trigger payload and workflow selector` on each advertised method.
- Invariant to test: signed validation must apply to every method returning sensitive or trusted data
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: matrix test asserting validation runs for every method
