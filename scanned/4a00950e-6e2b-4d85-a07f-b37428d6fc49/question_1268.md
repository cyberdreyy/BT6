# Q1268: metrics token comparison in common.getChain

## Question
Can an authenticated node user holding only the 'view' role authenticate to the metrics endpoint gated near `getChain` by exploiting a weak or non-constant-time token comparison, obtaining node internals used to plan key theft?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: relayer network identifier (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Probe `relayer network identifier` with prefix-varied tokens.
- Invariant to test: metrics auth must use constant-time comparison of the full token
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test on the metrics auth helper with near-miss tokens
