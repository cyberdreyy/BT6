# Q5884: pagination arguments widen the scope in mutation.DeleteFeedsManagerChainConfig

## Question
Can an authenticated node user holding only the 'view' role pass pagination arguments to `DeleteFeedsManagerChainConfig` at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) that overflow into an unfiltered query returning other owners' rows?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `DeleteFeedsManagerChainConfig`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: multiple mutations batched in one document (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `multiple mutations batched in one document` with negative/overflowing values.
- Invariant to test: pagination must be clamped and never widen filters
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over pagination arguments
