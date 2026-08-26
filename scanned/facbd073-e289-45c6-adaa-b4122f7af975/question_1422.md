# Q1422: state-changing request without origin binding in cookies.FindSessionCookie

## Question
Can a page loaded by a logged-in operator cause an unauthenticated HTTP client that can reach the node API port's chosen state change at the Cookie header on any authenticated /v2 route through `FindSessionCookie` because the session cookie alone authorizes the mutation?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: multiple clsession cookies in one header (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Auto-submit `multiple clsession cookies in one header` from an attacker page targeting a key-export or transfer route.
- Invariant to test: state-changing requests must require a non-cookie credential or origin binding
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test issuing a cross-site style request with only a session cookie
