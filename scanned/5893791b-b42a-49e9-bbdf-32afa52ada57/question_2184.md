# Q2184: double decoding of identifiers in common.getChain

## Question
Is an identifier decoded twice between the authorization check and the lookup on the path through `getChain`, letting an authenticated node user holding only the 'view' role authorize one object at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes and act on another?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: relayer network identifier (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `relayer network identifier` percent-encoded so the two stages resolve to different values.
- Invariant to test: the value authorized and the value used must be byte-identical
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the authorized identifier equals the identifier passed to the store
