# Q1183: debug route reachable below intended role in router.NewRouter

## Question
Is a debug/pprof/metrics route wired near `NewRouter` reachable by an unauthenticated HTTP client that can reach the node API port at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688), exposing node memory, goroutine dumps or command lines containing key passwords?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `NewRouter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: the route path and HTTP verb (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `route path and HTTP verb` against the debug group with a low-privilege session.
- Invariant to test: debug endpoints must require the highest role and never be reachable unauthenticated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test hitting each debug path with view-role and anonymous sessions
