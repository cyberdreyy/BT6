# Q0225: identifier normalization mismatch in handler.NewHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit workflow identifiers at web-API trigger and outgoing-request handling on the public gateway user endpoint whose normalization in `NewHandler` differs from the form used for authorization or accounting, so one identity authorizes and another executes?

## Target
- File/function: [core/services/gateway/handlers/capabilities/handler.go](core/services/gateway/handlers/capabilities/handler.go) -> `NewHandler`
- Entrypoint: web-API trigger and outgoing-request handling on the public gateway user endpoint
- Attacker controls: target URL/headers of the outgoing HTTP request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `target URL/headers of the outgoing HTTP request` in mixed-case/0x-less/padded hex.
- Invariant to test: the canonical form must be computed once and reused for both decisions
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting one canonical form is used for auth and execution
