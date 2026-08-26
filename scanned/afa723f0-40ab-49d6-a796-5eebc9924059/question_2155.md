# Q2155: replay across time, don or method in handshake.UnpackSignedAuthHeader

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests capture a signed request at the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange) and replay it through `UnpackSignedAuthHeader` for another DON, method or later time because no nonce/expiry binds it?

## Target
- File/function: [core/services/gateway/network/handshake.go](core/services/gateway/network/handshake.go) -> `UnpackSignedAuthHeader`
- Entrypoint: the gateway WebSocket auth handshake (PackAuthHeader/UnpackSignedAuthHeader/challenge exchange)
- Attacker controls: timestamp/nonce fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `timestamp/nonce fields` against other donIds/methods.
- Invariant to test: each signed request must be single-use and bound to don, method and a validity window
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a captured message and asserting rejection
