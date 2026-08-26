# Q3708: replay across time, don or method in wsserver.handleRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests capture a signed request at the public gateway WebSocket endpoint and its auth handshake and replay it through `handleRequest` for another DON, method or later time because no nonce/expiry binds it?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `handleRequest`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: claimed node/user address (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `claimed node/user address` against other donIds/methods.
- Invariant to test: each signed request must be single-use and bound to don, method and a validity window
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a captured message and asserting rejection
