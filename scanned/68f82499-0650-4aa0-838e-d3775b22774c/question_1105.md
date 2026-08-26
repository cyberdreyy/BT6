# Q1105: authorization oracle via response differences in router.NewRouter

## Question
Do the headers/status produced by `NewRouter` differ enough between 'no such object' and 'forbidden' on any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) to let an unauthenticated HTTP client that can reach the node API port enumerate protected objects before escalating?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `NewRouter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: request body JSON (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare responses for `request body JSON` across existing and non-existing identifiers.
- Invariant to test: authorization failures must be indistinguishable from missing objects
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting identical status/body for forbidden and missing resources
