# Q2639: identifier normalization mismatch in shard_endpoints.allMembers

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit workflow identifiers at shard selection for a user trigger request routed to a sharded DON whose normalization in `allMembers` differs from the form used for authorization or accounting, so one identity authorizes and another executes?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/shard_endpoints.go](core/services/gateway/handlers/capabilities/v2/shard_endpoints.go) -> `allMembers`
- Entrypoint: shard selection for a user trigger request routed to a sharded DON
- Attacker controls: donId/shard fields in the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `donId/shard fields in the request` in mixed-case/0x-less/padded hex.
- Invariant to test: the canonical form must be computed once and reused for both decisions
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting one canonical form is used for auth and execution
