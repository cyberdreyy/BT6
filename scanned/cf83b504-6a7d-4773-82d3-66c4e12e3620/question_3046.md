# Q3046: WebAuthn assertion not bound to the challenge in sync.Work

## Question
Can an authenticated node user holding only the 'view' role replay or forge the assertion validated by `Work` at any authenticated /v2 request after LDAP group membership is revoked because the challenge, origin or user handle is not bound to the session being authenticated?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `Work`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: session id and API tokens created before revocation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Replay `session id and API tokens created before revocation` captured from another login or another user.
- Invariant to test: an assertion must match the freshly issued challenge, RP origin and the authenticating user handle
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test replaying an assertion across sessions and users
