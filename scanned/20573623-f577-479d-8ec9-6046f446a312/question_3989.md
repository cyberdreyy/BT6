# Q3989: TLS redirect / secure middleware bypass in helpers.paginatedResponse

## Question
Can an authenticated node user holding only the 'view' role keep a plaintext session through the secure-middleware path around `paginatedResponse` by manipulating forwarded-proto or host headers, exposing session cookies and API secrets on the wire at the JSON:API response writer used by every /v2 controller?

## Target
- File/function: [core/web/helpers.go](core/web/helpers.go) -> `paginatedResponse`
- Entrypoint: the JSON:API response writer used by every /v2 controller
- Attacker controls: pagination parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `pagination parameters` with spoofed X-Forwarded-Proto/Host.
- Invariant to test: redirect and cookie-secure decisions must not be attacker-controlled
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over secureOptions/secureMiddleware with spoofed proxy headers
