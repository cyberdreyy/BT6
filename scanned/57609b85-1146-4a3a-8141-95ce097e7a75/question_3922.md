# Q3922: verb/method override in auth.AuthenticateByToken

## Question
Does routing near `AuthenticateByToken` honour a method-override header or map an unexpected verb onto a state-changing handler, letting a holder of a restricted API access-key/secret pair reach a write path through a read-gated route at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateByToken`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the target route and role wrapper reached (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `target route and role wrapper reached` using HEAD/OPTIONS or an override header against write routes.
- Invariant to test: handler selection must depend only on the real HTTP method
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test asserting non-declared verbs return 404/405 without executing the handler
