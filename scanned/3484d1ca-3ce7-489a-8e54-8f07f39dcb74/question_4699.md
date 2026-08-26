# Q4699: response correlation by attacker-chosen key in handshake.PackChallenge

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests receive another user's response because correlation in `PackChallenge` at the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange) uses a caller-supplied key rather than a server-generated one?

## Target
- File/function: [core/services/gateway/network/handshake.go](core/services/gateway/network/handshake.go) -> `PackChallenge`
- Entrypoint: the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange)
- Attacker controls: timestamp/nonce fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `timestamp/nonce fields` matching a victim's correlation key.
- Invariant to test: correlation keys must be server-generated and sender-scoped
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting responses are delivered only to the originating connection
