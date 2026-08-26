# Q3173: MFA store cookie forgeable in client.CreateEphemeralConnection

## Question
Is the WebAuthn session-store cookie handled around `CreateEphemeralConnection` unauthenticated or unsigned, letting an unauthenticated HTTP client that can reach the node API port craft one at POST /sessions against the configured LDAP server to complete an MFA step for another user?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `CreateEphemeralConnection`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: bind DN template inputs (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `bind DN template inputs` with attacker-chosen contents.
- Invariant to test: the MFA session store must be server-side or authenticated
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting a tampered store cookie is rejected
