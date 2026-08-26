# Q4169: undecodable responses counted as valid in requestcache.ProcessResponse

## Question
Does `ProcessResponse` at the gateway request cache keyed per user request count undecodable or error responses toward success, letting any internet client with an arbitrary externally-owned key sending signed gateway requests force a result with fewer honest contributions?

## Target
- File/function: [core/services/gateway/handlers/common/requestcache.go](core/services/gateway/handlers/common/requestcache.go) -> `ProcessResponse`
- Entrypoint: the gateway request cache keyed per user request
- Attacker controls: repeat and concurrent submissions (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger the mixed-response branch with `repeat and concurrent submissions`.
- Invariant to test: only successfully decoded, verified responses may count
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test with mixed decodable/undecodable responses
