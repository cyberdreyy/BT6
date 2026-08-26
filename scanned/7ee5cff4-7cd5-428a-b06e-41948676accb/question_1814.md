# Q1814: verb/method override in common.getChain

## Question
Does routing near `getChain` honour a method-override header or map an unexpected verb onto a state-changing handler, letting an authenticated node user holding only the 'view' role reach a write path through a read-gated route at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: chain id formatting (leading zeros, alternate base) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `chain id formatting (leading zeros, alternate base)` using HEAD/OPTIONS or an override header against write routes.
- Invariant to test: handler selection must depend only on the real HTTP method
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting non-declared verbs return 404/405 without executing the handler
