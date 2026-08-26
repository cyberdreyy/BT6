# Q2645: identifier normalization mismatch in callback.Wait

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit workflow identifiers at the callback used to return a DON response to the originating gateway user whose normalization in `Wait` differs from the form used for authorization or accounting, so one identity authorizes and another executes?

## Target
- File/function: [core/services/gateway/handlers/common/callback.go](core/services/gateway/handlers/common/callback.go) -> `Wait`
- Entrypoint: the callback used to return a DON response to the originating gateway user
- Attacker controls: duplicate responses for one request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `duplicate responses for one request` in mixed-case/0x-less/padded hex.
- Invariant to test: the canonical form must be computed once and reused for both decisions
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting one canonical form is used for auth and execution
