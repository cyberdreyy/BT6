# Q3519: signature not covering all authorization fields in wsserver.handleRequest

## Question
Does the signature validated on the path through `handleRequest` at the public gateway WebSocket endpoint and its auth handshake cover every field used for authorization (sender, method, donId, receiver, payload), or can any internet client with an arbitrary externally-owned key sending signed gateway requests mutate an uncovered field after signing?

## Target
- File/function: [core/services/gateway/network/wsserver.go](core/services/gateway/network/wsserver.go) -> `handleRequest`
- Entrypoint: the public gateway WebSocket endpoint and its auth handshake
- Attacker controls: the auth header presented at handshake (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Sign one message, then alter `auth header presented at handshake` before sending.
- Invariant to test: the signed digest must commit to every field later used for routing or authorization
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test mutating each field of a signed message and asserting rejection
