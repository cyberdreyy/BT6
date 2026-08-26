# Q1356: token claims trusted without verification in sync.NewLDAPServerStateSyncer

## Question
Does the identity token processed by `NewLDAPServerStateSyncer` at any authenticated /v2 request after LDAP group membership is revoked get accepted with unverified signature, issuer, audience or expiry, letting an authenticated node user holding only the 'view' role present a self-issued token and become an admin?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `NewLDAPServerStateSyncer`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: session id and API tokens created before revocation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `session id and API tokens created before revocation` signed by an attacker key or with alg/kid manipulated.
- Invariant to test: identity tokens must be verified against the configured issuer keys, audience and expiry
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test presenting self-signed and expired tokens
