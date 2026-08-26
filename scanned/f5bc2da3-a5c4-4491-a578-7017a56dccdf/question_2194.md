# Q2194: session store keyed on user input in sync.NewLDAPServerStateSyncer

## Question
Is any session/MFA store keyed by a value an authenticated node user holding only the 'view' role supplies at any authenticated /v2 request after LDAP group membership is revoked on the path through `NewLDAPServerStateSyncer`, allowing collision with another user's entry?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `NewLDAPServerStateSyncer`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: timing between group revocation and the sync tick (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `timing between group revocation and the sync tick` chosen to collide with an operator's key.
- Invariant to test: server-side session state must be keyed by an unguessable server-generated id
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting store keys are server-generated
