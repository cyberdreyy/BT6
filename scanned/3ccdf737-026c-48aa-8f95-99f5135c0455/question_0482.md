# Q0482: GraphQL mutation reaches unguarded resolver in common.getChain

## Question
Can an authenticated node user holding only the 'view' role invoke a state-changing resolver behind `getChain` at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes because the role check is applied at the HTTP layer rather than per-resolver?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: evmChainID query/body value (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Post a document using `evmChainID query/body value` that selects an admin-only mutation from a view-role session.
- Invariant to test: every mutation resolver must independently assert its minimum role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: resolver test executing each mutation with a view-role session and asserting an authorization error
