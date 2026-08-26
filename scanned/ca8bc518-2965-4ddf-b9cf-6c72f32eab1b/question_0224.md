# Q0224: identifier normalization mismatch in webapi.Validate

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit workflow identifiers at the web-API capability handler config validation and request path from the public gateway endpoint whose normalization in `Validate` differs from the form used for authorization or accounting, so one identity authorizes and another executes?

## Target
- File/function: [core/services/gateway/handlers/capabilities/webapi.go](core/services/gateway/handlers/capabilities/webapi.go) -> `Validate`
- Entrypoint: the web-API capability handler config validation and request path from the public gateway endpoint
- Attacker controls: rate/quota-relevant fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `rate/quota-relevant fields` in mixed-case/0x-less/padded hex.
- Invariant to test: the canonical form must be computed once and reused for both decisions
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting one canonical form is used for auth and execution
