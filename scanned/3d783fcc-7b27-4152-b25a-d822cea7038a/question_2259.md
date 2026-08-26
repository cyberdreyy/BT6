# Q2259: error path leaves partial authentication in session.NewSession

## Question
Does a failure after partial authentication in `NewSession` at POST /sessions (session creation) and API-token authentication still persist a session row or set a cookie usable by an unauthenticated HTTP client that can reach the node API port?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `NewSession`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: supplied access key and secret (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the late failure using `supplied access key and secret`.
- Invariant to test: no session artifact may survive a failed authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting no session row/cookie after each failure branch
