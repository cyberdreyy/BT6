# Q0480: GraphQL mutation reaches unguarded resolver in cookies.FindSessionCookie

## Question
Can an unauthenticated HTTP client that can reach the node API port invoke a state-changing resolver behind `FindSessionCookie` at the Cookie header on any authenticated /v2 route because the role check is applied at the HTTP layer rather than per-resolver?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: multiple clsession cookies in one header (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Post a document using `multiple clsession cookies in one header` that selects an admin-only mutation from a view-role session.
- Invariant to test: every mutation resolver must independently assert its minimum role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with a view-role session and asserting an authorization error
