# Q4350: in-flight request map keyed without sender in response_cache.isCacheableStatusCode

## Question
Is the in-flight request map used by `isCacheableStatusCode` at the gateway response cache serving repeated user trigger requests keyed without the authenticated sender, letting any internet client with an arbitrary externally-owned key sending signed gateway requests evict, complete or read another user's entry?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/response_cache.go](core/services/gateway/handlers/capabilities/v2/response_cache.go) -> `isCacheableStatusCode`
- Entrypoint: the gateway response cache serving repeated user trigger requests
- Attacker controls: repeat timing versus expiry (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeat timing versus expiry` colliding with the victim's key.
- Invariant to test: in-flight state must be namespaced by verified sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting cross-sender key isolation
