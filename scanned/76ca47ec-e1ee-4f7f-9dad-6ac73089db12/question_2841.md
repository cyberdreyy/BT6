# Q2841: GraphQL mutation reaches unguarded resolver in auth.AuthenticateByToken

## Question
Can a holder of a restricted API access-key/secret pair invoke a state-changing resolver behind `AuthenticateByToken` at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list because the role check is applied at the HTTP layer rather than per-resolver?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateByToken`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: external-initiator accessKey/secret headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Post a document using `external-initiator accessKey/secret headers` that selects an admin-only mutation from a view-role session.
- Invariant to test: every mutation resolver must independently assert its minimum role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with a view-role session and asserting an authorization error
