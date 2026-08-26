# Q3174: MFA store cookie forgeable in sync.Work

## Question
Is the WebAuthn session-store cookie handled around `Work` unauthenticated or unsigned, letting an authenticated node user holding only the 'view' role craft one at any authenticated /v2 request after LDAP group membership is revoked to complete an MFA step for another user?

## Target
- File/function: [core/sessions/ldapauth/sync.go](core/sessions/ldapauth/sync.go) -> `Work`
- Entrypoint: any authenticated /v2 request after LDAP group membership is revoked
- Attacker controls: session id and API tokens created before revocation (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `session id and API tokens created before revocation` with attacker-chosen contents.
- Invariant to test: the MFA session store must be server-side or authenticated
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting a tampered store cookie is rejected
