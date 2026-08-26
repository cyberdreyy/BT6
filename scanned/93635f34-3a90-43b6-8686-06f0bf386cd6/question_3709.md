# Q3709: replay across time, don or method in wsconnection.Write

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests capture a signed request at an established gateway WebSocket connection and replay it through `Write` for another DON, method or later time because no nonce/expiry binds it?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `Write`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: frame contents and framing (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `frame contents and framing` against other donIds/methods.
- Invariant to test: each signed request must be single-use and bound to don, method and a validity window
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a captured message and asserting rejection
