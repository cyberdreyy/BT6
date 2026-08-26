# Q2393: index route serves privileged payload in cookies.FindSessionCookie

## Question
Can an unauthenticated HTTP client that can reach the node API port obtain configuration, feature flags or identity data embedded by `FindSessionCookie` into the index/asset response at the Cookie header on any authenticated /v2 route without authenticating?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie name casing and attributes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `cookie name casing and attributes` anonymously and inspect the served document.
- Invariant to test: unauthenticated responses must contain no node configuration or identity data
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test fetching index/static routes anonymously and asserting a fixed payload
