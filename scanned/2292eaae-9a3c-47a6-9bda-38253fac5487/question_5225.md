# Q5225: message id chosen by the caller in connectionmanager.StartHandshake

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests choose a message/request id at the gateway node-facing handshake and connection registry as observed from a user request that collides with another user's in-flight request tracked via `StartHandshake`, so responses are cross-delivered?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `StartHandshake`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: handshake timing and repetition (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `handshake timing and repetition` reusing a victim's id.
- Invariant to test: request identity must include the authenticated sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test issuing two requests with the same id from different senders
