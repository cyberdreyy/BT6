# Q1890: TLS redirect / secure middleware bypass in cookies.FindSessionCookie

## Question
Can an unauthenticated HTTP client that can reach the node API port keep a plaintext session through the secure-middleware path around `FindSessionCookie` by manipulating forwarded-proto or host headers, exposing session cookies and API secrets on the wire at the Cookie header on any authenticated /v2 route?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: multiple clsession cookies in one header (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `multiple clsession cookies in one header` with spoofed X-Forwarded-Proto/Host.
- Invariant to test: redirect and cookie-secure decisions must not be attacker-controlled
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over secureOptions/secureMiddleware with spoofed proxy headers
