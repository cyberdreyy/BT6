# Q0006: authenticator precedence confusion in cookies.FindSessionCookie

## Question
Can an unauthenticated HTTP client that can reach the node API port send one request to the Cookie header on any authenticated /v2 route carrying both a crafted external-initiator credential and a session cookie so that the authenticator list reached by `FindSessionCookie` attributes the request to the stronger identity instead of failing closed?

## Target
- File/function: [core/web/cookies.go](core/web/cookies.go) -> `FindSessionCookie`
- Entrypoint: the Cookie header on any authenticated /v2 route
- Attacker controls: multiple clsession cookies in one header (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `multiple clsession cookies in one header` so an earlier authenticator errors and a later one succeeds while the request context still holds the first identity.
- Invariant to test: exactly one authenticator may establish identity, and a failed attempt must never leave a usable identity in the gin context
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over auth.Authenticate with mixed credential sets asserting the resolved user for each combination
