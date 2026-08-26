# Q5940: resolver executes before auth on error in mutation.DeleteFeedsManagerChainConfig

## Question
Does `DeleteFeedsManagerChainConfig` at POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains) perform its side effect before its role assertion returns, so an authenticated node user holding only the 'view' role still causes the change while receiving an authorization error?

## Target
- File/function: [core/web/resolver/mutation.go](core/web/resolver/mutation.go) -> `DeleteFeedsManagerChainConfig`
- Entrypoint: POST /query mutation resolvers (bridges, keys, feeds managers, jobs, chains)
- Attacker controls: id arguments referencing other users' objects (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `id arguments referencing other users' objects` and inspect state afterwards.
- Invariant to test: authorization must complete before any side effect
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test asserting no state change accompanies an authorization error
