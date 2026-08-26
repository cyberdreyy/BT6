# Q5318: password-gated mutation without password check in mutation.DeleteFeedsManagerChainConfig

## Question
Does the credential-issuing mutation `DeleteFeedsManagerChainConfig` at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) accept a missing/empty password field, letting an authenticated node user holding only the 'view' role mint an API token from a hijacked or low-role session?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `DeleteFeedsManagerChainConfig`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: the mutation name and input object (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `mutation name and input object` with empty/absent password.
- Invariant to test: credential-issuing mutations must re-verify the caller's password
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: resolver test with empty password asserting failure
