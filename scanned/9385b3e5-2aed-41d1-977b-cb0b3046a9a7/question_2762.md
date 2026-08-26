# Q2762: request id derivation collides in handler.sendHTTPMessageToClient

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests construct two distinct requests at web-API trigger and outgoing-request handling on the public gateway user endpoint that derive the same request id in `sendHTTPMessageToClient`, so one user's response is delivered to the other?

## Target
- File/function: [core/services/gateway/handlers/capabilities/handler.go](core/services/gateway/handlers/capabilities/handler.go) -> `sendHTTPMessageToClient`
- Entrypoint: web-API trigger and outgoing-request handling on the public gateway user endpoint
- Attacker controls: the trigger payload and workflow selector (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `trigger payload and workflow selector` varying a field excluded from the derivation.
- Invariant to test: the request id must be a collision-resistant function of every authorization-relevant field including the sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting distinct requests always derive distinct ids
