# Q1812: verb/method override in cookies.FindSessionCookie

## Question
Does routing near `FindSessionCookie` honour a method-override header or map an unexpected verb onto a state-changing handler, letting an unauthenticated HTTP client that can reach the node API port reach a write path through a read-gated route at the Cookie header on any authenticated /v2 route?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie value encoding (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `cookie value encoding` using HEAD/OPTIONS or an override header against write routes.
- Invariant to test: handler selection must depend only on the real HTTP method
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting non-declared verbs return 404/405 without executing the handler
