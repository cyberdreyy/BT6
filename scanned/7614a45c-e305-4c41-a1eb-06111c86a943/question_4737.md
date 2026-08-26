# Q4737: chain/node mutation reachable below role in auth.authenticateUserIsAdmin

## Question
Can an authenticated node user holding only the 'view' role mutate chain or node configuration through `authenticateUserIsAdmin` at POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin (RPC URL, enabled flag) with a low role, redirecting the node to an attacker-controlled data source?

## Target
- File/function: [core/web/resolver/auth.go](core/web/resolver/auth.go) -> `authenticateUserIsAdmin`
- Entrypoint: POST /query resolvers wrapped by authenticateUserCanRun/CanEdit/IsAdmin
- Attacker controls: variables (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `variables` pointing at an attacker endpoint.
- Invariant to test: chain/node configuration mutations require the admin role
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: resolver test mutating node config from low-role sessions
