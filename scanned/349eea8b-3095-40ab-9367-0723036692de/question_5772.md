# Q5772: chain/node mutation reachable below role in mutation.DeleteFeedsManagerChainConfig

## Question
Can an authenticated node user holding only the 'view' role mutate chain or node configuration through `DeleteFeedsManagerChainConfig` at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) (RPC URL, enabled flag) with a low role, redirecting the node to an attacker-controlled data source?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `DeleteFeedsManagerChainConfig`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: id arguments referencing other users' objects (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `id arguments referencing other users' objects` pointing at an attacker endpoint.
- Invariant to test: chain/node configuration mutations require the admin role
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: resolver test mutating node config from low-role sessions
