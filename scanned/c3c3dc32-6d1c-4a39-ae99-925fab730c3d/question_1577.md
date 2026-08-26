# Q1577: external-initiator credential over-scoped in helpers.jsonAPIError

## Question
Can an unauthenticated HTTP client that can reach the node API port use an external-initiator credential accepted by `jsonAPIError` on any /v2 or /query error response path to reach routes beyond the single job-run endpoint it was issued for?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `jsonAPIError`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: unknown IDs and type parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `unknown IDs and type parameters` against other /v2 routes sharing the authenticator list.
- Invariant to test: an EI credential must authorize only run-triggering for jobs bound to that initiator
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: route test presenting EI credentials against every /v2 route
