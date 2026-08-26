# Q4544: nil user with nil error in auth.AuthenticateExternalInitiator

## Question
Can a holder of a restricted API access-key/secret pair reach a branch of `AuthenticateExternalInitiator` on any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list where the authenticator returns a zero-valued user together with a nil error, so the request continues with an empty identity that later role checks treat as satisfied?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateExternalInitiator`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the session cookie value (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `session cookie value` to hit an early-return path that forgets to set the error before returning.
- Invariant to test: no code path may return a usable session/user value alongside a nil error
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting every error branch of the authenticator returns a non-nil error and a zero user
