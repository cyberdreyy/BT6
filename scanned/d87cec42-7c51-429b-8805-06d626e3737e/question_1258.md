# Q1258: secret identifier traversal in requestcache.NewRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests address another namespace or owner through identifier separators/encoding in the request validated by `NewRequest` at the gateway request cache keyed per user request?

## Target
- File/function: [core/services/gateway/handlers/common/requestcache.go](core/services/gateway/handlers/common/requestcache.go) -> `NewRequest`
- Entrypoint: the gateway request cache keyed per user request
- Attacker controls: the request id/key fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request id/key fields` with separators, encoded delimiters or empty components.
- Invariant to test: identifier components must be validated and joined unambiguously
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test over identifier parsing with hostile components
