# Q5480: token claims trusted without verification in ldap.FindUserByAPIToken

## Question
Does the identity token processed by `FindUserByAPIToken` at POST /sessions when the LDAP authentication provider is configured get accepted with unverified signature, issuer, audience or expiry, letting an unauthenticated HTTP client that can reach the node API port present a self-issued token and become an admin?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `FindUserByAPIToken`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: password bytes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `password bytes` signed by an attacker key or with alg/kid manipulated.
- Invariant to test: identity tokens must be verified against the configured issuer keys, audience and expiry
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test presenting self-signed and expired tokens
