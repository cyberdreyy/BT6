# Q4739: chain/node mutation reachable below role in user.NewUpdatePasswordPayload

## Question
Can an authenticated node user holding only the 'view' role mutate chain or node configuration through `NewUpdatePasswordPayload` at POST /query updateUserPassword mutation and user query (RPC URL, enabled flag) with a low role, redirecting the node to an attacker-controlled data source?

## Target
- File/function: [core/web/resolver/user.go](core/web/resolver/user.go) -> `NewUpdatePasswordPayload`
- Entrypoint: POST /query updateUserPassword mutation and user query
- Attacker controls: oldPassword/newPassword input (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `oldPassword/newPassword input` pointing at an attacker endpoint.
- Invariant to test: chain/node configuration mutations require the admin role
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: resolver test mutating node config from low-role sessions
