# Q3619: claim used for identity is attacker-settable in client.CreateEphemeralConnection

## Question
Is the claim mapped to the node account by `CreateEphemeralConnection` at POST /sessions against the configured LDAP server one the attacker can set at the identity provider (email without verification, name, preferred_username), letting an unauthenticated HTTP client that can reach the node API port collide with an operator account?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `CreateEphemeralConnection`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: connection reuse across logins (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Register `connection reuse across logins` at the IdP matching an operator's identifier.
- Invariant to test: account binding must use an immutable, verified claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting the binding claim and its verification requirement
