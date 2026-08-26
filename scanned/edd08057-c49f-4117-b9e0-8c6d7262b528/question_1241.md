# Q1241: routing field selects an unauthorized DON in codec.Codec

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests set the donId/receiver at the encode/decode boundary for gateway user requests and responses so `Codec` routes their request to a DON they are not entitled to use, consuming its capacity or capabilities?

## Target
- File/function: [core/services/gateway/api/codec.go](core/services/gateway/api/codec.go) -> `Codec`
- Entrypoint: the encode/decode boundary for gateway user requests and responses
- Attacker controls: the raw request bytes (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `raw request bytes` naming another DON.
- Invariant to test: DON routing must be validated against the sender's entitlement
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test routing requests to unauthorized DON ids
