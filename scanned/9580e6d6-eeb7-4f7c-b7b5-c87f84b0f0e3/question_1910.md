# Q1910: password-gated mutation without password check in query.Bridges

## Question
Does the credential-issuing mutation `Bridges` at POST /query read resolvers (bridges, jobs, keys, config, nodes, features) accept a missing/empty password field, letting an authenticated node user holding only the 'view' role mint an API token from a hijacked or low-role session?

## Target
- File/function: [core/web/resolver/query.go](core/web/resolver/query.go) -> `Bridges`
- Entrypoint: POST /query read resolvers (bridges, jobs, keys, config, nodes, features)
- Attacker controls: the queried field and arguments (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `queried field and arguments` with empty/absent password.
- Invariant to test: credential-issuing mutations must re-verify the caller's password
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test with empty password asserting failure
