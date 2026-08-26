# Q0877: secret disclosure through error body in common.getChain

## Question
Does an error path reached from the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes through `getChain` serialize internal values (config secrets, DB DSN, key material, tokens) into the JSON:API error returned to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: chain id formatting (leading zeros, alternate base) (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the error branch with `chain id formatting (leading zeros, alternate base)` and inspect the returned detail string.
- Invariant to test: error responses must contain no server-side secret or connection string
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies match an allowlist of messages
