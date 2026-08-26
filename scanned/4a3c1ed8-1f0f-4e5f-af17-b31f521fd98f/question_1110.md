# Q1110: authorization oracle via response differences in cookies.FindSessionCookie

## Question
Do the headers/status produced by `FindSessionCookie` differ enough between 'no such object' and 'forbidden' on the Cookie header on any authenticated /v2 route to let an unauthenticated HTTP client that can reach the node API port enumerate protected objects before escalating?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie value encoding (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Compare responses for `cookie value encoding` across existing and non-existing identifiers.
- Invariant to test: authorization failures must be indistinguishable from missing objects
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting identical status/body for forbidden and missing resources
