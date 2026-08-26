# Q0394: request id derivation collides in callback.SendResponse

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests construct two distinct requests at the callback used to return a DON response to the originating gateway user that derive the same request id in `SendResponse`, so one user's response is delivered to the other?

## Target
- File/function: [core/services/gateway/handlers/common/callback.go](core/services/gateway/handlers/common/callback.go) -> `SendResponse`
- Entrypoint: the callback used to return a DON response to the originating gateway user
- Attacker controls: duplicate responses for one request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `duplicate responses for one request` varying a field excluded from the derivation.
- Invariant to test: the request id must be a collision-resistant function of every authorization-relevant field including the sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting distinct requests always derive distinct ids
