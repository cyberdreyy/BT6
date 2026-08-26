# Q5866: TLS redirect / secure middleware bypass in gql.GetGQLAuthenticatedSession

## Question
Can an authenticated node user holding only the 'view' role keep a plaintext session through the secure-middleware path around `GetGQLAuthenticatedSession` by manipulating forwarded-proto or host headers, exposing session cookies and API secrets on the wire at POST /query (GraphQL) guarded by AuthenticateGQL?

## Target
- File/function: [core/web/auth/gql.go](core/web/auth/gql.go) -> `GetGQLAuthenticatedSession`
- Entrypoint: POST /query (GraphQL) guarded by AuthenticateGQL
- Attacker controls: the GraphQL document (query/mutation/alias/fragment) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `GraphQL document (query/mutation/alias/fragment)` with spoofed X-Forwarded-Proto/Host.
- Invariant to test: redirect and cookie-secure decisions must not be attacker-controlled
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over secureOptions/secureMiddleware with spoofed proxy headers
