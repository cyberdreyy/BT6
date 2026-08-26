# Q2952: routing field selects an unauthorized DON in utils.BytesToUint32

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests set the donId/receiver at the encoding/signing helpers used on every gateway message before authorization so `BytesToUint32` routes their request to a DON they are not entitled to use, consuming its capacity or capabilities?

## Target
- File/function: [core/services/gateway/common/utils.go](core/services/gateway/common/utils.go) -> `BytesToUint32`
- Entrypoint: the encoding/signing helpers used on every gateway message before authorization
- Attacker controls: strings and byte lengths that hit alignment/padding helpers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `strings and byte lengths that hit alignment/padding helpers` naming another DON.
- Invariant to test: DON routing must be validated against the sender's entitlement
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test routing requests to unauthorized DON ids
