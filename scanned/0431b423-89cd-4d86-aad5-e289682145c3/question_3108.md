# Q3108: WebAuthn registration bound to the wrong user in ldap.FindUser

## Question
Can an unauthenticated HTTP client that can reach the node API port register a credential through `FindUser` at POST /sessions when the LDAP authentication provider is configured that becomes attached to another user's account, giving permanent MFA-satisfying access?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `FindUser`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: group membership values returned for the DN (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `group membership values returned for the DN` with a user handle or session store cookie referring to a different account.
- Invariant to test: the registered credential must attach to the authenticated session's user only
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test asserting the stored credential's user id equals the session user
