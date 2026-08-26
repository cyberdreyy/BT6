# Q2945: routing field selects an unauthorized DON in wsconnection.Reset

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests set the donId/receiver at an established gateway WebSocket connection so `Reset` routes their request to a DON they are not entitled to use, consuming its capacity or capabilities?

## Target
- File/function: [core/services/gateway/network/wsconnection.go](core/services/gateway/network/wsconnection.go) -> `Reset`
- Entrypoint: an established gateway WebSocket connection
- Attacker controls: frame contents and framing (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `frame contents and framing` naming another DON.
- Invariant to test: DON routing must be validated against the sender's entitlement
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test routing requests to unauthorized DON ids
