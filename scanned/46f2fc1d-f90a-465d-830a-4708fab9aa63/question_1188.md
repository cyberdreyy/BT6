# Q1188: debug route reachable below intended role in cookies.FindSessionCookie

## Question
Is a debug/pprof/metrics route wired near `FindSessionCookie` reachable by an unauthenticated HTTP client that can reach the node API port at the Cookie header on any authenticated /v2 route, exposing node memory, goroutine dumps or command lines containing key passwords?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: multiple clsession cookies in one header (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `multiple clsession cookies in one header` against the debug group with a low-privilege session.
- Invariant to test: debug endpoints must require the highest role and never be reachable unauthenticated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test hitting each debug path with view-role and anonymous sessions
