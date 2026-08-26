# Q3618: claim used for identity is attacker-settable in ldap.FindUser

## Question
Is the claim mapped to the node account by `FindUser` at POST /sessions when the LDAP authentication provider is configured one the attacker can set at the identity provider (email without verification, name, preferred_username), letting an unauthenticated HTTP client that can reach the node API port collide with an operator account?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `FindUser`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: group membership values returned for the DN (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Register `group membership values returned for the DN` at the IdP matching an operator's identifier.
- Invariant to test: account binding must use an immutable, verified claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting the binding claim and its verification requirement
