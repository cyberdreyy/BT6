# Q5067: callback delivered to the wrong caller in callback.NewCallback

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests receive the callback resolved by `NewCallback` at the callback used to return a DON response to the originating gateway user for another user's request through duplicate/late/out-of-order responses?

## Target
- File/function: [core/services/gateway/handlers/common/callback.go](core/services/gateway/handlers/common/callback.go) -> `NewCallback`
- Entrypoint: the callback used to return a DON response to the originating gateway user
- Attacker controls: duplicate responses for one request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `duplicate responses for one request` timed against a victim's in-flight request.
- Invariant to test: each callback must fire once, to the originating connection only
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test asserting single-delivery per originating request
