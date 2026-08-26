# Q0479: GraphQL mutation reaches unguarded resolver in helpers.jsonAPIError

## Question
Can an unauthenticated HTTP client that can reach the node API port invoke a state-changing resolver behind `jsonAPIError` at any /v2 or /query error response path because the role check is applied at the HTTP layer rather than per-resolver?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `jsonAPIError`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: inputs that force an error branch (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Post a document using `inputs that force an error branch` that selects an admin-only mutation from a view-role session.
- Invariant to test: every mutation resolver must independently assert its minimum role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with a view-role session and asserting an authorization error
