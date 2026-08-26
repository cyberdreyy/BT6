# Q5064: callback delivered to the wrong caller in handler.NewHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests receive the callback resolved by `NewHandler` at the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint for another user's request through duplicate/late/out-of-order responses?

## Target
- File/function: [core/services/gateway/handlers/vault/handler.go](core/services/gateway/handlers/vault/handler.go) -> `NewHandler`
- Entrypoint: the vault gateway methods (secrets create/update/get/list, DKG) on the public user endpoint
- Attacker controls: owner/namespace/secret identifier fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `owner/namespace/secret identifier fields` timed against a victim's in-flight request.
- Invariant to test: each callback must fire once, to the originating connection only
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test asserting single-delivery per originating request
