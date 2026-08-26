# Q0778: callback delivered to the wrong caller in handler.NewHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests receive the callback resolved by `NewHandler` at web-API trigger and outgoing-request handling on the public gateway user endpoint for another user's request through duplicate/late/out-of-order responses?

## Target
- File/function: [core/services/gateway/handlers/capabilities/handler.go](core/services/gateway/handlers/capabilities/handler.go) -> `NewHandler`
- Entrypoint: web-API trigger and outgoing-request handling on the public gateway user endpoint
- Attacker controls: messageId used for callback correlation (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `messageId used for callback correlation` timed against a victim's in-flight request.
- Invariant to test: each callback must fire once, to the originating connection only
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test asserting single-delivery per originating request
