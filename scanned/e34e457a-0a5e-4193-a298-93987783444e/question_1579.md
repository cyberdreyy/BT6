# Q1579: external-initiator credential over-scoped in api.ParsePaginatedRequest

## Question
Can an authenticated node user holding only the 'view' role use an external-initiator credential accepted by `ParsePaginatedRequest` on page/size query parameters on /v2 index endpoints to reach routes beyond the single job-run endpoint it was issued for?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `ParsePaginatedRequest`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: JSON:API document fields in the request body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `JSON:API document fields in the request body` against other /v2 routes sharing the authenticator list.
- Invariant to test: an EI credential must authorize only run-triggering for jobs bound to that initiator
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: route test presenting EI credentials against every /v2 route
