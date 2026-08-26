# Q4255: password-gated mutation without password check in auth.authenticateUserIsAdmin

## Question
Does the credential-issuing mutation `authenticateUserIsAdmin` at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin accept a missing/empty password field, letting an authenticated node user holding only the 'view' role mint an API token from a hijacked or low-role session?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUserIsAdmin`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: the resolver selected by the GraphQL document (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `resolver selected by the GraphQL document` with empty/absent password.
- Invariant to test: credential-issuing mutations must re-verify the caller's password
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test with empty password asserting failure
