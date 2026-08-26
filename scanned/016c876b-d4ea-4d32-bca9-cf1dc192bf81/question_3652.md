# Q3652: signature malleability accepted in connectionmanager.DONConnectionManager

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests present a malleable or alternative-encoding signature at the gateway node-facing handshake and connection registry as observed from a user request that `DONConnectionManager` accepts, producing a second valid form of an existing request (replay under a new id)?

## Target
- File/function: [core/services/gateway/connectionmanager.go](core/services/gateway/connectionmanager.go) -> `DONConnectionManager`
- Entrypoint: the gateway node-facing handshake and connection registry as observed from a user request
- Attacker controls: donId in user requests routed to a DON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `donId in user requests routed to a DON` with high-S/alternate v/padded r-s values.
- Invariant to test: signature encoding must be canonical and single-valued
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test over ExtractSigner with malleable signatures
