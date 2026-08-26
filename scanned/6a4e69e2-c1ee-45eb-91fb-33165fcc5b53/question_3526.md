# Q3526: signature not covering all authorization fields in connectionmanager.DONConnectionManager

## Question
Does the signature validated on the path through `DONConnectionManager` at the gateway node-facing handshake and connection registry as observed from a user request cover every field used for authorization (sender, method, donId, receiver, payload), or can any internet client with an arbitrary externally-owned key sending signed gateway requests mutate an uncovered field after signing?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `DONConnectionManager`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: the claimed address in the handshake (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Sign one message, then alter `claimed address in the handshake` before sending.
- Invariant to test: the signed digest must commit to every field later used for routing or authorization
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test mutating each field of a signed message and asserting rejection
