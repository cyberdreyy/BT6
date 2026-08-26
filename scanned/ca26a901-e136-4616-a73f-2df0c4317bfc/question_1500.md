# Q1500: empty or absent credential accepted in cookies.FindSessionCookie

## Question
Does `FindSessionCookie` treat an empty access key, empty secret or empty session id presented at the Cookie header on any authenticated /v2 route as a match against an unset/zero stored value, authenticating an unauthenticated HTTP client that can reach the node API port as a real identity?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie name casing and attributes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `cookie name casing and attributes` with empty or omitted credential fields.
- Invariant to test: empty credentials must always fail authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/absent credential fields asserting 401
