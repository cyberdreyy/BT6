# Q1112: authorization oracle via response differences in common.getChain

## Question
Do the headers/status produced by `getChain` differ enough between 'no such object' and 'forbidden' on the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes to let an authenticated node user holding only the 'view' role enumerate protected objects before escalating?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: chain id formatting (leading zeros, alternate base) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare responses for `chain id formatting (leading zeros, alternate base)` across existing and non-existing identifiers.
- Invariant to test: authorization failures must be indistinguishable from missing objects
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting identical status/body for forbidden and missing resources
