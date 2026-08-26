# Q0324: non-constant-time credential comparison in common.getChain

## Question
Does the credential comparison reached by `getChain` from the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes short-circuit on the first differing byte, letting an authenticated node user holding only the 'view' role recover a valid API/EI secret by measuring response timing across requests?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: relayer network identifier (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send many requests varying `relayer network identifier` one byte at a time and rank by latency.
- Invariant to test: all secret comparisons must be constant time over the full secret
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: benchmark/timing test over the comparison helper with prefix-matching secrets
