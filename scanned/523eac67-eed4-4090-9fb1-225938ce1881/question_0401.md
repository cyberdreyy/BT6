# Q0401: multiple session cookies in cookies.FindSessionCookie

## Question
If an unauthenticated HTTP client that can reach the node API port sends two clsession cookies on the Cookie header on any authenticated /v2 route, does the lookup used by `FindSessionCookie` pick the attacker-supplied one while later code trusts the other, producing a session-identity mismatch?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: cookie value encoding (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `cookie value encoding` with duplicate cookie names in one header.
- Invariant to test: exactly one session cookie must be considered and duplicates must be rejected
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test issuing duplicate Cookie headers and asserting a 401
