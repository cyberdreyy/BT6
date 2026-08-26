# Q4314: error path leaves partial authentication in sessions_controller.Create

## Question
Does a failure after partial authentication in `Create` at POST /sessions and DELETE /sessions still persist a session row or set a cookie usable by an unauthenticated HTTP client that can reach the node API port?

## Target
- File/function: [core/web/sessions_controller.go](core/web/sessions_controller.go) -> `Create`
- Entrypoint: POST /sessions and DELETE /sessions
- Attacker controls: the session cookie returned/echoed (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the late failure using `session cookie returned/echoed`.
- Invariant to test: no session artifact may survive a failed authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting no session row/cookie after each failure branch
