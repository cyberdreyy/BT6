# Q4299: stale role after change in auth.AuthenticateByToken

## Question
Does a session or token validated through `AuthenticateByToken` keep its old role at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list after the role was downgraded or the user deleted, letting a holder of a restricted API access-key/secret pair act with revoked privileges?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateByToken`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the session cookie value (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Continue sending `session cookie value` on the existing session after the change.
- Invariant to test: role and existence must be re-read from the store on every request
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test downgrading a role mid-session and asserting the next request is rejected
