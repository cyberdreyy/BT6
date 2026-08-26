# Q5848: routing field selects an unauthorized DON in connectionmanager.StartHandshake

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests set the donId/receiver at the gateway node-facing handshake and connection registry as observed from a user request so `StartHandshake` routes their request to a DON they are not entitled to use, consuming its capacity or capabilities?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `StartHandshake`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: the claimed address in the handshake (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `claimed address in the handshake` naming another DON.
- Invariant to test: DON routing must be validated against the sender's entitlement
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test routing requests to unauthorized DON ids
