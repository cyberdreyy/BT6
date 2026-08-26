# Q1548: response correlation by attacker-chosen key in wsserver.NewWebSocketServer

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests receive another user's response because correlation in `NewWebSocketServer` at the public gateway WebSocket endpoint and its auth handshake uses a caller-supplied key rather than a server-generated one?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `NewWebSocketServer`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: claimed node/user address (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `claimed node/user address` matching a victim's correlation key.
- Invariant to test: correlation keys must be server-generated and sender-scoped
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting responses are delivered only to the originating connection
