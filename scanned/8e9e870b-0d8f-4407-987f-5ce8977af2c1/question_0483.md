# Q0483: GraphQL mutation reaches unguarded resolver in helpers.jsonAPIError

## Question
Can an authenticated node user holding only the 'view' role invoke a state-changing resolver behind `jsonAPIError` at the JSON:API response writer used by every /v2 controller because the role check is applied at the HTTP layer rather than per-resolver?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `jsonAPIError`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: inputs that select the error branch (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Post a document using `inputs that select the error branch` that selects an admin-only mutation from a view-role session.
- Invariant to test: every mutation resolver must independently assert its minimum role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with a view-role session and asserting an authorization error
