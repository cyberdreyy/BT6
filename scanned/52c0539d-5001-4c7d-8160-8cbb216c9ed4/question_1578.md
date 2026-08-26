# Q1578: external-initiator credential over-scoped in cookies.FindSessionCookie

## Question
Can an unauthenticated HTTP client that can reach the node API port use an external-initiator credential accepted by `FindSessionCookie` on the Cookie header on any authenticated /v2 route to reach routes beyond the single job-run endpoint it was issued for?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie value encoding (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `cookie value encoding` against other /v2 routes sharing the authenticator list.
- Invariant to test: an EI credential must authorize only run-triggering for jobs bound to that initiator
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: route test presenting EI credentials against every /v2 route
