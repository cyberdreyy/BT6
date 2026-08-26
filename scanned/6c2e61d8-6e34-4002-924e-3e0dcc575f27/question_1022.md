# Q1022: signature validation optional by method in handler.addResponseForNode

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests select a method at the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint for which `addResponseForNode` skips signed-response validation, receiving an unverified or attacker-influenceable result?

## Target
- File/function: [core/services/gateway/handlers/vault/handler.go](core/services/gateway/handlers/vault/handler.go) -> `addResponseForNode`
- Entrypoint: the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint
- Attacker controls: the vault method and request payload (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `vault method and request payload` on each advertised method.
- Invariant to test: signed validation must apply to every method returning sensitive or trusted data
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: matrix test asserting validation runs for every method
