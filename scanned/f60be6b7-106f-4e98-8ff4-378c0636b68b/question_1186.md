# Q1186: debug route reachable below intended role in gql.AuthenticateGQL

## Question
Is a debug/pprof/metrics route wired near `AuthenticateGQL` reachable by an authenticated node user holding only the 'view' role at POST /query (GraphQL) guarded by AuthenticateGQL, exposing node memory, goroutine dumps or command lines containing key passwords?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `AuthenticateGQL`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: batched operations in one request (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `batched operations in one request` against the debug group with a low-privilege session.
- Invariant to test: debug endpoints must require the highest role and never be reachable unauthenticated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test hitting each debug path with view-role and anonymous sessions
