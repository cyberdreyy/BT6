# Q2947: routing field selects an unauthorized DON in message.Sign

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests set the donId/receiver at the signed gateway message envelope submitted to the public user endpoint so `Sign` routes their request to a DON they are not entitled to use, consuming its capacity or capabilities?

## Target
- File/function: [core/services/gateway/api/message.go](core/services/gateway/api/message.go) -> `Sign`
- Entrypoint: the signed gateway message envelope submitted to the public user endpoint
- Attacker controls: every MessageBody field (sender, method, donId, messageId, payload) (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `every MessageBody field (sender, method, donId, messageId, payload)` naming another DON.
- Invariant to test: DON routing must be validated against the sender's entitlement
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test routing requests to unauthorized DON ids
