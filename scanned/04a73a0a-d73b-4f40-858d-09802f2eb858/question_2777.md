# Q2777: multiple session cookies in auth.AuthenticateByToken

## Question
If a holder of a restricted API access-key/secret pair sends two clsession cookies on any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list, does the lookup used by `AuthenticateByToken` pick the attacker-supplied one while later code trusts the other, producing a session-identity mismatch?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateByToken`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the session cookie value (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `session cookie value` with duplicate cookie names in one header.
- Invariant to test: exactly one session cookie must be considered and duplicates must be rejected
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test issuing duplicate Cookie headers and asserting a 401
