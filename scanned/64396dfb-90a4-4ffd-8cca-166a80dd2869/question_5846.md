# Q5846: routing field selects an unauthorized DON in gateway.ProcessRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests set the donId/receiver at ProcessRequest on the public gateway user endpoint so `ProcessRequest` routes their request to a DON they are not entitled to use, consuming its capacity or capabilities?

## Target
- File/function: [core/services/gateway/gateway.go](core/services/gateway/gateway.go) -> `ProcessRequest`
- Entrypoint: ProcessRequest on the public gateway user endpoint
- Attacker controls: method and donId routing fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `method and donId routing fields` naming another DON.
- Invariant to test: DON routing must be validated against the sender's entitlement
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test routing requests to unauthorized DON ids
