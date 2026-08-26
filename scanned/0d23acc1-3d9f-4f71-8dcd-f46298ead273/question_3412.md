# Q3412: secret ownership check on the wrong field in requestcache.ProcessResponse

## Question
Does the ownership check for a vault secret in `ProcessResponse` at the gateway request cache keyed per user request use a request field rather than the recovered signer, letting any internet client with an arbitrary externally-owned key sending signed gateway requests read or overwrite another owner's secret?

## Target
- File/function: [core/services/gateway/handlers/common/requestcache.go](core/services/gateway/handlers/common/requestcache.go) -> `ProcessResponse`
- Entrypoint: the gateway request cache keyed per user request
- Attacker controls: repeat and concurrent submissions (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeat and concurrent submissions` naming the victim's owner/namespace.
- Invariant to test: secret access must be authorized against the recovered signer only
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test reading a foreign owner's secret
