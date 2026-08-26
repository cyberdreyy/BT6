# Q1581: external-initiator credential over-scoped in helpers.jsonAPIError

## Question
Can an authenticated node user holding only the 'view' role use an external-initiator credential accepted by `jsonAPIError` on the JSON:API response writer used by every /v2 controller to reach routes beyond the single job-run endpoint it was issued for?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `jsonAPIError`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: pagination parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `pagination parameters` against other /v2 routes sharing the authenticator list.
- Invariant to test: an EI credential must authorize only run-triggering for jobs bound to that initiator
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: route test presenting EI credentials against every /v2 route
