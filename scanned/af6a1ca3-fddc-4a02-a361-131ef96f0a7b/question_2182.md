# Q2182: double decoding of identifiers in cookies.FindSessionCookie

## Question
Is an identifier decoded twice between the authorization check and the lookup on the path through `FindSessionCookie`, letting an unauthenticated HTTP client that can reach the node API port authorize one object at the Cookie header on any authenticated /v2 route and act on another?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie name casing and attributes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `cookie name casing and attributes` percent-encoded so the two stages resolve to different values.
- Invariant to test: the value authorized and the value used must be byte-identical
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test asserting the authorized identifier equals the identifier passed to the store
