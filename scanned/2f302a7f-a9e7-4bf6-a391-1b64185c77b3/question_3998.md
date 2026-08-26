# Q3998: token lookup ignores scope in sync.Work

## Question
Does the API token lookup performed by `Work` at any authenticated /v2 request after LDAP group membership is revoked return a user without checking the token's owner, expiry or state, letting an authenticated node user holding only the 'view' role present a deleted user's token?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `Work`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: timing between group revocation and the sync tick (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `timing between group revocation and the sync tick` belonging to a deleted or downgraded account.
- Invariant to test: token authentication must re-validate the owning account's existence and role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a token after its owner is deleted
