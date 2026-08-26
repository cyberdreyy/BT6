# Q0475: GraphQL mutation reaches unguarded resolver in router.NewRouter

## Question
Can an unauthenticated HTTP client that can reach the node API port invoke a state-changing resolver behind `NewRouter` at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688) because the role check is applied at the HTTP layer rather than per-resolver?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `NewRouter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: Authorization / X-API-KEY headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Post a document using `Authorization / X-API-KEY headers` that selects an admin-only mutation from a view-role session.
- Invariant to test: every mutation resolver must independently assert its minimum role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with a view-role session and asserting an authorization error
