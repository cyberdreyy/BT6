# Q5223: message id chosen by the caller in gateway.ProcessRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests choose a message/request id at ProcessRequest on the public gateway user endpoint that collides with another user's in-flight request tracked via `ProcessRequest`, so responses are cross-delivered?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `ProcessRequest`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: the message payload (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `message payload` reusing a victim's id.
- Invariant to test: request identity must include the authenticated sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test issuing two requests with the same id from different senders
