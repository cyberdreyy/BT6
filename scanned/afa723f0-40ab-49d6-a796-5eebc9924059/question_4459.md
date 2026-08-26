# Q4459: routing field selects an unauthorized DON in httpserver.handleHealthCheck

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests set the donId/receiver at the public gateway user HTTP endpoint (POST to the configured user path) so `handleHealthCheck` routes their request to a DON they are not entitled to use, consuming its capacity or capabilities?

## Target
- File/function: [core/services/gateway/network/httpserver.go](core/services/gateway/network/httpserver.go) -> `handleHealthCheck`
- Entrypoint: the public gateway user HTTP endpoint (POST to the configured user path)
- Attacker controls: request repetition rate (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request repetition rate` naming another DON.
- Invariant to test: DON routing must be validated against the sender's entitlement
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test routing requests to unauthorized DON ids
