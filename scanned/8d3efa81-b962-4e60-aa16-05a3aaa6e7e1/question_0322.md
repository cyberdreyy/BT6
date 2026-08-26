# Q0322: non-constant-time credential comparison in cookies.FindSessionCookie

## Question
Does the credential comparison reached by `FindSessionCookie` from the Cookie header on any authenticated /v2 route short-circuit on the first differing byte, letting an unauthenticated HTTP client that can reach the node API port recover a valid API/EI secret by measuring response timing across requests?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie name casing and attributes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send many requests varying `cookie name casing and attributes` one byte at a time and rank by latency.
- Invariant to test: all secret comparisons must be constant time over the full secret
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: benchmark/timing test over the comparison helper with prefix-matching secrets
