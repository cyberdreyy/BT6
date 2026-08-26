# Q2459: authenticator precedence confusion in helpers.addForbiddenErrorHeaders

## Question
Can an unauthenticated HTTP client that can reach the node API port send one request to any /v2 or /query error response path carrying both a crafted external-initiator credential and a session cookie so that the authenticator list reached by `addForbiddenErrorHeaders` attributes the request to the stronger identity instead of failing closed?

## Target
- File/function: [core/web/auth/helpers.go](core/web/auth/helpers.go) -> `addForbiddenErrorHeaders`
- Entrypoint: any /v2 or /query error response path
- Attacker controls: unknown IDs and type parameters (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `unknown IDs and type parameters` so an earlier authenticator errors and a later one succeeds while the request context still holds the first identity.
- Invariant to test: exactly one authenticator may establish identity, and a failed attempt must never leave a usable identity in the gin context
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over auth.Authenticate with mixed credential sets asserting the resolved user for each combination
