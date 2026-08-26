# Q1891: TLS redirect / secure middleware bypass in api.ParsePaginatedRequest

## Question
Can an authenticated node user holding only the 'view' role keep a plaintext session through the secure-middleware path around `ParsePaginatedRequest` by manipulating forwarded-proto or host headers, exposing session cookies and API secrets on the wire at page/size query parameters on /v2 index endpoints?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `ParsePaginatedRequest`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: page and size query values (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `page and size query values` with spoofed X-Forwarded-Proto/Host.
- Invariant to test: redirect and cookie-secure decisions must not be attacker-controlled
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over secureOptions/secureMiddleware with spoofed proxy headers
