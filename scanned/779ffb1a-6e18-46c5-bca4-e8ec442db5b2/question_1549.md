# Q1549: response correlation by attacker-chosen key in wsconnection.NewWSConnectionWrapper

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests receive another user's response because correlation in `NewWSConnectionWrapper` at an established gateway WebSocket connection uses a caller-supplied key rather than a server-generated one?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `NewWSConnectionWrapper`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: connection reset/reconnect timing (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `connection reset/reconnect timing` matching a victim's correlation key.
- Invariant to test: correlation keys must be server-generated and sender-scoped
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting responses are delivered only to the originating connection
