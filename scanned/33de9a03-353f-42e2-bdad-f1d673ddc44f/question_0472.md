# Q0472: caller-supplied request id in requestcache.NewRequest

## Question
Does `NewRequest` at the gateway request cache keyed per user request accept a caller-chosen request id, letting any internet client with an arbitrary externally-owned key sending signed gateway requests bind to or overwrite an in-flight request from another user?

## Target
- File/function: [core/services/gateway/handlers/common/requestcache.go](core/services/gateway/handlers/common/requestcache.go) -> `NewRequest`
- Entrypoint: the gateway request cache keyed per user request
- Attacker controls: response arrival ordering (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `response arrival ordering` reusing a victim's id.
- Invariant to test: request ids must be server-generated or sender-scoped
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test submitting a duplicate id from a different sender
