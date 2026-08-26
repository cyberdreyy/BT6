# Q3731: external-initiator credential over-scoped in router.graphqlHandler

## Question
Can an unauthenticated HTTP client that can reach the node API port use an external-initiator credential accepted by `graphqlHandler` on any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) to reach routes beyond the single job-run endpoint it was issued for?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `graphqlHandler`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: the session cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `session cookie` against other /v2 routes sharing the authenticator list.
- Invariant to test: an EI credential must authorize only run-triggering for jobs bound to that initiator
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: route test presenting EI credentials against every /v2 route
