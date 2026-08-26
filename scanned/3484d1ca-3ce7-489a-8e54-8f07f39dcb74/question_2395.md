# Q2395: index route serves privileged payload in common.getChain

## Question
Can an authenticated node user holding only the 'view' role obtain configuration, feature flags or identity data embedded by `getChain` into the index/asset response at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes without authenticating?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: relayer network identifier (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `relayer network identifier` anonymously and inspect the served document.
- Invariant to test: unauthenticated responses must contain no node configuration or identity data
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test fetching index/static routes anonymously and asserting a fixed payload
