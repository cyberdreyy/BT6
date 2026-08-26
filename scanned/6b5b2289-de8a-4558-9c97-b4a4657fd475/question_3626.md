# Q3626: chain/node mutation reachable below role in api_token.Secret

## Question
Can an authenticated node user holding only the 'view' role mutate chain or node configuration through `Secret` at POST /query createAPIToken/deleteAPIToken mutations (RPC URL, enabled flag) with a low role, redirecting the node to an attacker-controlled data source?

## Target
- File/function: [core/web/resolver/api_token.go](core/web/resolver/api_token.go) -> `Secret`
- Entrypoint: POST /query createAPIToken/deleteAPIToken mutations
- Attacker controls: the returned token fields selected (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `returned token fields selected` pointing at an attacker endpoint.
- Invariant to test: chain/node configuration mutations require the admin role
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: resolver test mutating node config from low-role sessions
