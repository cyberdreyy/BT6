# Q1892: TLS redirect / secure middleware bypass in common.getChain

## Question
Can an authenticated node user holding only the 'view' role keep a plaintext session through the secure-middleware path around `getChain` by manipulating forwarded-proto or host headers, exposing session cookies and API secrets on the wire at the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes?

## Target
- File/function: [core/web/common.go](core/web/common.go) -> `getChain`
- Entrypoint: the evmChainID/chain selector parameter accepted by /v2 chain-scoped routes
- Attacker controls: evmChainID query/body value (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `evmChainID query/body value` with spoofed X-Forwarded-Proto/Host.
- Invariant to test: redirect and cookie-secure decisions must not be attacker-controlled
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over secureOptions/secureMiddleware with spoofed proxy headers
