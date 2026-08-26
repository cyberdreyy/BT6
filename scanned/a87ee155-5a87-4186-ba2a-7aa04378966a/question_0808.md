# Q0808: WebAuthn registration bound to the wrong user in sync.NewLDAPServerStateSyncer

## Question
Can an authenticated node user holding only the 'view' role register a credential through `NewLDAPServerStateSyncer` at any authenticated /v2 request after LDAP group membership is revoked that becomes attached to another user's account, giving permanent MFA-satisfying access?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `NewLDAPServerStateSyncer`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: timing between group revocation and the sync tick (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `timing between group revocation and the sync tick` with a user handle or session store cookie referring to a different account.
- Invariant to test: the registered credential must attach to the authenticated session's user only
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the stored credential's user id equals the session user
