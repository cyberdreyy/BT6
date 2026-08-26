# Q5219: message id chosen by the caller in wsconnection.ReadChannel

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests choose a message/request id at an established gateway WebSocket connection that collides with another user's in-flight request tracked via `ReadChannel`, so responses are cross-delivered?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `ReadChannel`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: connection reset/reconnect timing (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `connection reset/reconnect timing` reusing a victim's id.
- Invariant to test: request identity must include the authenticated sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test issuing two requests with the same id from different senders
