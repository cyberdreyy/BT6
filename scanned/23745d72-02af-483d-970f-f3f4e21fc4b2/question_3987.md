# Q3987: TLS redirect / secure middleware bypass in helpers.addForbiddenErrorHeaders

## Question
Can an unauthenticated HTTP client that can reach the node API port keep a plaintext session through the secure-middleware path around `addForbiddenErrorHeaders` by manipulating forwarded-proto or host headers, exposing session cookies and API secrets on the wire at any /v2 or /query error response path?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `addForbiddenErrorHeaders`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: unknown IDs and type parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `unknown IDs and type parameters` with spoofed X-Forwarded-Proto/Host.
- Invariant to test: redirect and cookie-secure decisions must not be attacker-controlled
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over secureOptions/secureMiddleware with spoofed proxy headers
