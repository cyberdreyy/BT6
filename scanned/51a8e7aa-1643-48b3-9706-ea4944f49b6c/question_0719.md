# Q0719: static asset path traversal in common.getChain

## Question
Can an authenticated node user holding only the 'view' role escape the embedded asset root through `getChain` at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes and read node files such as TLS keys, keystore files or the config secrets file?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: evmChainID query/body value (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Request `evmChainID query/body value` containing encoded dot-segments, backslashes or unicode separators.
- Invariant to test: asset serving must be confined to the embedded filesystem regardless of input encoding
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the path resolver with traversal payloads asserting no host file is opened
