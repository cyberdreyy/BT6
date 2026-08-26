# Q5863: TLS redirect / secure middleware bypass in router.rateLimiter

## Question
Can an unauthenticated HTTP client that can reach the node API port keep a plaintext session through the secure-middleware path around `rateLimiter` by manipulating forwarded-proto or host headers, exposing session cookies and API secrets on the wire at any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)?

## Target
- File/function: [core/web/router.go](core/web/router.go) -> `rateLimiter`
- Entrypoint: any route registered by NewRouter/v2Routes/sessionRoutes/loopRoutes on the node API listener (default :6688)
- Attacker controls: Origin and X-Forwarded-For headers (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `Origin and X-Forwarded-For headers` with spoofed X-Forwarded-Proto/Host.
- Invariant to test: redirect and cookie-secure decisions must not be attacker-controlled
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over secureOptions/secureMiddleware with spoofed proxy headers
