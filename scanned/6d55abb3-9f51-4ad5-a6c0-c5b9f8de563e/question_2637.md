# Q2637: identifier normalization mismatch in workflow_metadata_handler.Authorize

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit workflow identifiers at the workflow metadata/authorization lookup consulted for every user trigger request whose normalization in `Authorize` differs from the form used for authorization or accounting, so one identity authorizes and another executes?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `Authorize`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: authorization key material presented (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `authorization key material presented` in mixed-case/0x-less/padded hex.
- Invariant to test: the canonical form must be computed once and reused for both decisions
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting one canonical form is used for auth and execution
