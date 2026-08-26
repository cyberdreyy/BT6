# Q2839: GraphQL mutation reaches unguarded resolver in router.graphqlHandler

## Question
Can an unauthenticated HTTP client that can reach the node API port invoke a state-changing resolver behind `graphqlHandler` at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) because the role check is applied at the HTTP layer rather than per-resolver?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `graphqlHandler`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: Origin and X-Forwarded-For headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Post a document using `Origin and X-Forwarded-For headers` that selects an admin-only mutation from a view-role session.
- Invariant to test: every mutation resolver must independently assert its minimum role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with a view-role session and asserting an authorization error
