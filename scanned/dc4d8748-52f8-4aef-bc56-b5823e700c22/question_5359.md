# Q5359: debug route reachable below intended role in helpers.paginatedRequest

## Question
Is a debug/pprof/metrics route wired near `paginatedRequest` reachable by an authenticated node user holding only the 'view' role at the JSON:API response writer used by every /v2 controller, exposing node memory, goroutine dumps or command lines containing key passwords?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedRequest`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: requested resource type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `requested resource type` against the debug group with a low-privilege session.
- Invariant to test: debug endpoints must require the highest role and never be reachable unauthenticated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test hitting each debug path with view-role and anonymous sessions
