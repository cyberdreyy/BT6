# Q5921: session fixation in auth.AuthenticateExternalInitiator

## Question
Does the session id observed on the path through `AuthenticateExternalInitiator` survive privilege changes at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list, letting a holder of a restricted API access-key/secret pair pre-seed a session id that becomes privileged after the victim logs in?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateExternalInitiator`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the session cookie value (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Plant `session cookie value` and observe whether the id is regenerated on successful login.
- Invariant to test: a new session identifier must be issued on every successful authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the session id before and after login differ
