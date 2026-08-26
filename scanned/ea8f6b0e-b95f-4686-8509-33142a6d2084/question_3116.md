# Q3116: password-gated mutation without password check in api_token.Secret

## Question
Does the credential-issuing mutation `Secret` at POST /query createAPIToken/deleteAPIToken mutations accept a missing/empty password field, letting an authenticated node user holding only the 'view' role mint an API token from a hijacked or low-role session?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `Secret`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: the password field in the mutation input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `password field in the mutation input` with empty/absent password.
- Invariant to test: credential-issuing mutations must re-verify the caller's password
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test with empty password asserting failure
