# Q1266: metrics token comparison in cookies.FindSessionCookie

## Question
Can an unauthenticated HTTP client that can reach the node API port authenticate to the metrics endpoint gated near `FindSessionCookie` by exploiting a weak or non-constant-time token comparison, obtaining node internals used to plan key theft?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie name casing and attributes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Probe `cookie name casing and attributes` with prefix-varied tokens.
- Invariant to test: metrics auth must use constant-time comparison of the full token
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: unit test on the metrics auth helper with near-miss tokens
