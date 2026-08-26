# Q2643: identifier normalization mismatch in aggregator.signedResponseRequestIDEnabled

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit workflow identifiers at aggregation and signature/quorum validation of vault node responses before they reach the requesting user whose normalization in `signedResponseRequestIDEnabled` differs from the form used for authorization or accounting, so one identity authorizes and another executes?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `signedResponseRequestIDEnabled`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: method selection that toggles signed validation (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `method selection that toggles signed validation` in mixed-case/0x-less/padded hex.
- Invariant to test: the canonical form must be computed once and reused for both decisions
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting one canonical form is used for auth and execution
