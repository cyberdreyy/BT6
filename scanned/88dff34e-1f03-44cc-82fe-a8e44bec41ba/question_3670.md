# Q3670: empty or absent credential accepted in auth.AuthenticateByToken

## Question
Does `AuthenticateByToken` treat an empty access key, empty secret or empty session id presented at any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list as a match against an unset/zero stored value, authenticating a holder of a restricted API access-key/secret pair as a real identity?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateByToken`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: the target route and role wrapper reached (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Send `target route and role wrapper reached` with empty or omitted credential fields.
- Invariant to test: empty credentials must always fail authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test with empty/absent credential fields asserting 401
