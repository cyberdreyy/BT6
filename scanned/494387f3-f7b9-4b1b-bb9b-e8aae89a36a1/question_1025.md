# Q1025: signature validation optional by method in callback.SendResponse

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests select a method at the callback used to return a DON response to the originating gateway user for which `SendResponse` skips signed-response validation, receiving an unverified or attacker-influenceable result?

## Target
- File/function: [core/services/gateway/handlers/common/callback.go](core/services/gateway/handlers/common/callback.go) -> `SendResponse`
- Entrypoint: the callback used to return a DON response to the originating gateway user
- Attacker controls: identifiers used to select the callback (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `identifiers used to select the callback` on each advertised method.
- Invariant to test: signed validation must apply to every method returning sensitive or trusted data
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: matrix test asserting validation runs for every method
