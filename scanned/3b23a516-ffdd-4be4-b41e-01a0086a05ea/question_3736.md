# Q3736: external-initiator credential over-scoped in api.paginationLink

## Question
Can an authenticated node user holding only the 'view' role use an external-initiator credential accepted by `paginationLink` on page/size query parameters on /v2 index endpoints to reach routes beyond the single job-run endpoint it was issued for?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `paginationLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: Link header follow-up requests (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `Link header follow-up requests` against other /v2 routes sharing the authenticator list.
- Invariant to test: an EI credential must authorize only run-triggering for jobs bound to that initiator
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: route test presenting EI credentials against every /v2 route
