# Q4777: request id derivation collides in message_util.ValidatedResponseFromMessage

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests construct two distinct requests at validated conversion between gateway messages, requests and responses that derive the same request id in `ValidatedResponseFromMessage`, so one user's response is delivered to the other?

## Target
- File/function: [core/services/gateway/handlers/common/message_util.go](core/services/gateway/handlers/common/message_util.go) -> `ValidatedResponseFromMessage`
- Entrypoint: validated conversion between gateway messages, requests and responses
- Attacker controls: encoding variants of identifiers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `encoding variants of identifiers` varying a field excluded from the derivation.
- Invariant to test: the request id must be a collision-resistant function of every authorization-relevant field including the sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting distinct requests always derive distinct ids
