# Q1502: empty or absent credential accepted in common.getChain

## Question
Does `getChain` treat an empty access key, empty secret or empty session id presented at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes as a match against an unset/zero stored value, authenticating an authenticated node user holding only the 'view' role as a real identity?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: relayer network identifier (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `relayer network identifier` with empty or omitted credential fields.
- Invariant to test: empty credentials must always fail authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/absent credential fields asserting 401
