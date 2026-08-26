# Q1887: TLS redirect / secure middleware bypass in auth.AuthenticateBySession

## Question
Can a holder of a restricted API access-key/secret pair keep a plaintext session through the secure-middleware path around `AuthenticateBySession` by manipulating forwarded-proto or host headers, exposing session cookies and API secrets on the wire at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateBySession`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: X-API-KEY and X-API-SECRET headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `X-API-KEY and X-API-SECRET headers` with spoofed X-Forwarded-Proto/Host.
- Invariant to test: redirect and cookie-secure decisions must not be attacker-controlled
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over secureOptions/secureMiddleware with spoofed proxy headers
