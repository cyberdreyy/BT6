# Q1189: debug route reachable below intended role in api.ParsePaginatedRequest

## Question
Is a debug/pprof/metrics route wired near `ParsePaginatedRequest` reachable by an authenticated node user holding only the 'view' role at page/size query parameters on /v2 index endpoints, exposing node memory, goroutine dumps or command lines containing key passwords?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `ParsePaginatedRequest`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: page and size query values (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `page and size query values` against the debug group with a low-privilege session.
- Invariant to test: debug endpoints must require the highest role and never be reachable unauthenticated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test hitting each debug path with view-role and anonymous sessions
