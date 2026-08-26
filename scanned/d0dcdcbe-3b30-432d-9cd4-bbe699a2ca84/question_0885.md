# Q0885: MFA store cookie forgeable in ldap.NewLDAPAuthenticator

## Question
Is the WebAuthn session-store cookie handled around `NewLDAPAuthenticator` unauthenticated or unsigned, letting an unauthenticated HTTP client that can reach the node API port craft one at POST /sessions when the LDAP authentication provider is configured to complete an MFA step for another user?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `NewLDAPAuthenticator`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: session id (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `session id` with attacker-chosen contents.
- Invariant to test: the MFA session store must be server-side or authenticated
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting a tampered store cookie is rejected
