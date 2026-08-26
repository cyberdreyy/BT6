# Q4842: GraphQL mutation reaches unguarded resolver in helpers.paginatedRequest

## Question
Can an authenticated node user holding only the 'view' role invoke a state-changing resolver behind `paginatedRequest` at the JSON:API response writer used by every /v2 controller because the role check is applied at the HTTP layer rather than per-resolver?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedRequest`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: requested resource type (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Post a document using `requested resource type` that selects an admin-only mutation from a view-role session.
- Invariant to test: every mutation resolver must independently assert its minimum role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with a view-role session and asserting an authorization error
