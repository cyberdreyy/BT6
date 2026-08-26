# Q3494: state parameter not verified in sync.Work

## Question
Is the state/nonce checked by `Work` at any authenticated /v2 request after LDAP group membership is revoked unbound to the initiating browser session, letting an authenticated node user holding only the 'view' role inject their own authorization code and take over the resulting node session?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `Work`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: timing between group revocation and the sync tick (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Deliver `timing between group revocation and the sync tick` with an attacker-obtained code and a replayed state.
- Invariant to test: state must be single-use and bound to the initiating session
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test replaying state/code pairs across sessions
