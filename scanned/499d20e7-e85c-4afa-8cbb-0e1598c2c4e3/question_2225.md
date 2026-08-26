# Q2225: message id chosen by the caller in wsserver.handleHealthCheck

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests choose a message/request id at the public gateway WebSocket endpoint and its auth handshake that collides with another user's in-flight request tracked via `handleHealthCheck`, so responses are cross-delivered?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `handleHealthCheck`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: the auth header presented at handshake (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `auth header presented at handshake` reusing a victim's id.
- Invariant to test: request identity must include the authenticated sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test issuing two requests with the same id from different senders
