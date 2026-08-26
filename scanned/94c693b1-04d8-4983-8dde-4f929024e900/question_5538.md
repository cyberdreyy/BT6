# Q5538: claim used for identity is attacker-settable in sync.deleteStaleSessions

## Question
Is the claim mapped to the node account by `deleteStaleSessions` at any authenticated /v2 request after LDAP group membership is revoked one the attacker can set at the identity provider (email without verification, name, preferred_username), letting an authenticated node user holding only the 'view' role collide with an operator account?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `deleteStaleSessions`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: timing between group revocation and the sync tick (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Register `timing between group revocation and the sync tick` at the IdP matching an operator's identifier.
- Invariant to test: account binding must use an immutable, verified claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting the binding claim and its verification requirement
