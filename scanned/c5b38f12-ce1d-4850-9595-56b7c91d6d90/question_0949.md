# Q0949: pagination parameter injection in router.NewRouter

## Question
Can an unauthenticated HTTP client that can reach the node API port pass a crafted page/size value through `NewRouter` on any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) that reaches the query layer unvalidated and returns rows belonging to other users or unfiltered secret columns?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `NewRouter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: the session cookie (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `session cookie` with negative, overflowing or non-numeric values.
- Invariant to test: pagination inputs must be validated and never widen the row filter
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over ParsePaginatedRequest with hostile values asserting bounded output
