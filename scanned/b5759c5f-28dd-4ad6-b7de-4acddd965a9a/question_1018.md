# Q1018: signature validation optional by method in response_cache.newResponseCache

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests select a method at the gateway response cache serving repeated user trigger requests for which `newResponseCache` skips signed-response validation, receiving an unverified or attacker-influenceable result?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/response_cache.go](core/services/gateway/handlers/capabilities/v2/response_cache.go) -> `newResponseCache`
- Entrypoint: the gateway response cache serving repeated user trigger requests
- Attacker controls: the cache key fields of the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `cache key fields of the request` on each advertised method.
- Invariant to test: signed validation must apply to every method returning sensitive or trusted data
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: matrix test asserting validation runs for every method
