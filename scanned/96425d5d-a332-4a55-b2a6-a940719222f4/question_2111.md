# Q2111: wildcard parameter swallows a route in common.getChain

## Question
Does a wildcard/param segment on the path to `getChain` capture a more specific protected route so an authenticated node user holding only the 'view' role's request at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes is served by a handler with weaker checks?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: evmChainID query/body value (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `evmChainID query/body value` whose value equals another route's literal segment.
- Invariant to test: wildcard routes must not shadow explicitly registered protected routes
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting the expected handler runs for colliding paths
