# Q5354: debug route reachable below intended role in router.rateLimiter

## Question
Is a debug/pprof/metrics route wired near `rateLimiter` reachable by an unauthenticated HTTP client that can reach the node API port at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688), exposing node memory, goroutine dumps or command lines containing key passwords?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `rateLimiter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: request body JSON (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `request body JSON` against the debug group with a low-privilege session.
- Invariant to test: debug endpoints must require the highest role and never be reachable unauthenticated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test hitting each debug path with view-role and anonymous sessions
