# Q2344: delete/disable reachable below role in api_token.AccessKey

## Question
Can an authenticated node user holding only the 'view' role disable or delete an object through `AccessKey` at POST /query createAPIToken/deleteAPIToken mutations (feeds manager, bridge, key, job) with only view/run rights, degrading oracle reporting?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `AccessKey`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: the password field in the mutation input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `password field in the mutation input` from a low-role session.
- Invariant to test: destructive mutations require the admin role
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: resolver test invoking destructive mutations from low-role sessions
