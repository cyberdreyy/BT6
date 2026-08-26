# Q4487: authenticator precedence confusion in api.nextLink

## Question
Can an authenticated node user holding only the 'view' role send one request to page/size query parameters on /v2 index endpoints carrying both a crafted external-initiator credential and a session cookie so that the authenticator list reached by `nextLink` attributes the request to the stronger identity instead of failing closed?

## Target
- File/function: [core/web/api.go](core/web/api.go) -> `nextLink`
- Entrypoint: page/size query parameters on /v2 index endpoints
- Attacker controls: Link header follow-up requests (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `Link header follow-up requests` so an earlier authenticator errors and a later one succeeds while the request context still holds the first identity.
- Invariant to test: exactly one authenticator may establish identity, and a failed attempt must never leave a usable identity in the gin context
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over auth.Authenticate with mixed credential sets asserting the resolved user for each combination
