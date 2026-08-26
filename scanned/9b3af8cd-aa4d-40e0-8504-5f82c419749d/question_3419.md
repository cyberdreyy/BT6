# Q3419: debug route reachable below intended role in helpers.addForbiddenErrorHeaders

## Question
Is a debug/pprof/metrics route wired near `addForbiddenErrorHeaders` reachable by an unauthenticated HTTP client that can reach the node API port at any /v2 or /query error response path, exposing node memory, goroutine dumps or command lines containing key passwords?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `addForbiddenErrorHeaders`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: unknown IDs and type parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `unknown IDs and type parameters` against the debug group with a low-privilege session.
- Invariant to test: debug endpoints must require the highest role and never be reachable unauthenticated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test hitting each debug path with view-role and anonymous sessions
