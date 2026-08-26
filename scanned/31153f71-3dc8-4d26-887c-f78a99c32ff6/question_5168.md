# Q5168: replay across time, don or method in connectionmanager.StartHandshake

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests capture a signed request at the gateway node-facing handshake and connection registry as observed from a user request and replay it through `StartHandshake` for another DON, method or later time because no nonce/expiry binds it?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `StartHandshake`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: the claimed address in the handshake (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `claimed address in the handshake` against other donIds/methods.
- Invariant to test: each signed request must be single-use and bound to don, method and a validity window
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: test replaying a captured message and asserting rejection
