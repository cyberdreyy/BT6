# Q3406: secret ownership check on the wrong field in response_cache.isCacheableStatusCode

## Question
Does the ownership check for a vault secret in `isCacheableStatusCode` at the gateway response cache serving repeated user trigger requests use a request field rather than the recovered signer, letting any internet client with an arbitrary externally-owned key sending signed gateway requests read or overwrite another owner's secret?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/response_cache.go](core/services/gateway/handlers/capabilities/v2/response_cache.go) -> `isCacheableStatusCode`
- Entrypoint: the gateway response cache serving repeated user trigger requests
- Attacker controls: repeat timing versus expiry (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeat timing versus expiry` naming the victim's owner/namespace.
- Invariant to test: secret access must be authorized against the recovered signer only
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test reading a foreign owner's secret
