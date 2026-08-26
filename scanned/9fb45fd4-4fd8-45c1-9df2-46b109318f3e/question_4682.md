# Q4682: key-creating mutation reachable below role in mutation.CreateFeedsManagerChainConfig

## Question
Can an authenticated node user holding only the 'view' role create or import a key through `CreateFeedsManagerChainConfig` at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) without admin rights, planting a key the node will later sign with?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `CreateFeedsManagerChainConfig`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: multiple mutations batched in one document (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `multiple mutations batched in one document` with attacker-supplied key material.
- Invariant to test: key material mutations require the admin role
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: resolver test creating/importing keys from low-role sessions
