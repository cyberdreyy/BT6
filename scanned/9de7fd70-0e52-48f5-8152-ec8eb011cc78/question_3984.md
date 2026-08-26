# Q3984: TLS redirect / secure middleware bypass in middleware.Exists

## Question
Can an unauthenticated HTTP client that can reach the node API port keep a plaintext session through the secure-middleware path around `Exists` by manipulating forwarded-proto or host headers, exposing session cookies and API secrets on the wire at GET on any static asset path served by ServeGzippedAssets/GzipFileServer?

## Target
- File/function: [core/web/middleware.go](core/web/middleware.go) -> `Exists`
- Entrypoint: GET on any static asset path served by ServeGzippedAssets/GzipFileServer
- Attacker controls: the requested asset path (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `requested asset path` with spoofed X-Forwarded-Proto/Host.
- Invariant to test: redirect and cookie-secure decisions must not be attacker-controlled
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over secureOptions/secureMiddleware with spoofed proxy headers
