# Q1190: debug route reachable below intended role in common.getChain

## Question
Is a debug/pprof/metrics route wired near `getChain` reachable by an authenticated node user holding only the 'view' role at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes, exposing node memory, goroutine dumps or command lines containing key passwords?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: evmChainID query/body value (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `evmChainID query/body value` against the debug group with a low-privilege session.
- Invariant to test: debug endpoints must require the highest role and never be reachable unauthenticated
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: route test hitting each debug path with view-role and anonymous sessions
