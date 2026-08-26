# Q2761: request id derivation collides in handler.Callback

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests construct two distinct requests at the gateway handler interface boundary every public user request passes through that derive the same request id in `Callback`, so one user's response is delivered to the other?

## Target
- File/function: [core/services/gateway/handlers/handler.go](core/services/gateway/handlers/handler.go) -> `Callback`
- Entrypoint: the gateway handler interface boundary every public user request passes through
- Attacker controls: the method and payload of the user request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `method and payload of the user request` varying a field excluded from the derivation.
- Invariant to test: the request id must be a collision-resistant function of every authorization-relevant field including the sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting distinct requests always derive distinct ids
