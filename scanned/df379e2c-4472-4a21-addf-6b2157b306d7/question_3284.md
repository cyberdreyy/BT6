# Q3284: signature validation optional by method in requestcache.ProcessResponse

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests select a method at the gateway request cache keyed per user request for which `ProcessResponse` skips signed-response validation, receiving an unverified or attacker-influenceable result?

## Target
- File/function: [core/services/gateway/handlers/common/requestcache.go](core/services/gateway/handlers/common/requestcache.go) -> `ProcessResponse`
- Entrypoint: the gateway request cache keyed per user request
- Attacker controls: response arrival ordering (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `response arrival ordering` on each advertised method.
- Invariant to test: signed validation must apply to every method returning sensitive or trusted data
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: matrix test asserting validation runs for every method
