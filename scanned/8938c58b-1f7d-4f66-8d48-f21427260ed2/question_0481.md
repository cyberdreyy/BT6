# Q0481: GraphQL mutation reaches unguarded resolver in api.ParsePaginatedRequest

## Question
Can an authenticated node user holding only the 'view' role invoke a state-changing resolver behind `ParsePaginatedRequest` at page/size query parameters on /v2 index endpoints because the role check is applied at the HTTP layer rather than per-resolver?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `ParsePaginatedRequest`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: page and size query values (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Post a document using `page and size query values` that selects an admin-only mutation from a view-role session.
- Invariant to test: every mutation resolver must independently assert its minimum role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with a view-role session and asserting an authorization error
