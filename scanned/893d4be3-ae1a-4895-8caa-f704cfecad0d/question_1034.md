# Q1034: chain selector reaches unintended relayer in common.getChain

## Question
Can an authenticated node user holding only the 'view' role supply a chain identifier through `getChain` at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes that resolves to a relayer/keystore other than the one authorization was evaluated against?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: relayer network identifier (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `relayer network identifier` with alternate encodings of the chain id (leading zeros, whitespace, different base).
- Invariant to test: the chain resolved for execution must be the exact chain authorized for the request
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over getChain with equivalent-but-different chain id strings
