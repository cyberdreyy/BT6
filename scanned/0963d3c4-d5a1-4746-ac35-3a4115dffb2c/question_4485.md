# Q4485: authenticator precedence confusion in auth.AuthenticateExternalInitiator

## Question
Can a holder of a restricted API access-key/secret pair send one request to any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list carrying both a crafted external-initiator credential and a session cookie so that the authenticator list reached by `AuthenticateExternalInitiator` attributes the request to the stronger identity instead of failing closed?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateExternalInitiator`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: X-API-KEY and X-API-SECRET headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `X-API-KEY and X-API-SECRET headers` so an earlier authenticator errors and a later one succeeds while the request context still holds the first identity.
- Invariant to test: exactly one authenticator may establish identity, and a failed attempt must never leave a usable identity in the gin context
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over auth.Authenticate with mixed credential sets asserting the resolved user for each combination
