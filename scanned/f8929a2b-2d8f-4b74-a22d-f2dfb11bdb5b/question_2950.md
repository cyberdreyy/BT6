# Q2950: routing field selects an unauthorized DON in multihandler.HandleLegacyUserMessage

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests set the donId/receiver at gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests so `HandleLegacyUserMessage` routes their request to a DON they are not entitled to use, consuming its capacity or capabilities?

## Target
- File/function: [core/services/gateway/multihandler.go](core/services/gateway/multihandler.go) -> `HandleLegacyUserMessage`
- Entrypoint: gateway method routing (HandleLegacyUserMessage/HandleJSONRPCUserMessage) for user requests
- Attacker controls: the requested method name (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `requested method name` naming another DON.
- Invariant to test: DON routing must be validated against the sender's entitlement
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test routing requests to unauthorized DON ids
