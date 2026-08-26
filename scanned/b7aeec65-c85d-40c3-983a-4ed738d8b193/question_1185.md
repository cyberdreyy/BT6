# Q1185: debug route reachable below intended role in auth.AuthenticateBySession

## Question
Is a debug/pprof/metrics route wired near `AuthenticateBySession` reachable by a holder of a restricted API access-key/secret pair at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list, exposing node memory, goroutine dumps or command lines containing key passwords?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateBySession`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the target route and role wrapper reached (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `target route and role wrapper reached` against the debug group with a low-privilege session.
- Invariant to test: debug endpoints must require the highest role and never be reachable unauthenticated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test hitting each debug path with view-role and anonymous sessions
