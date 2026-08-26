# Q5697: identity overwritten downstream in auth.AuthenticateExternalInitiator

## Question
Can a later middleware or handler on the path through `AuthenticateExternalInitiator` overwrite the authenticated identity established at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list using a request-controlled field?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateExternalInitiator`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the session cookie value (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `session cookie value` whose name collides with the context key or session field used downstream.
- Invariant to test: the authenticated identity must be immutable after the auth middleware
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test injecting colliding body/header fields and asserting the identity is unchanged
