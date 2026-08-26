# Q1512: redirect target attacker-controlled in sync.NewLDAPServerStateSyncer

## Question
Can an authenticated node user holding only the 'view' role steer the post-authentication redirect handled near `NewLDAPServerStateSyncer` at any authenticated /v2 request after LDAP group membership is revoked to an external host, capturing the issued session cookie or code?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `NewLDAPServerStateSyncer`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: session id and API tokens created before revocation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Supply `session id and API tokens created before revocation` with an absolute or protocol-relative URL.
- Invariant to test: redirect targets must be restricted to a server-side allowlist
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the redirect validator with hostile URLs
