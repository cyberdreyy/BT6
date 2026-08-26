# Q2852: password change without old-password proof in ldap.FindUser

## Question
Can an unauthenticated HTTP client that can reach the node API port change the password (or set a new one) through the path reaching `FindUser` at POST /sessions when the LDAP authentication provider is configured without a verified old password or with the check applied to the wrong account?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `FindUser`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: group membership values returned for the DN (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `group membership values returned for the DN` naming another account or omitting the old-password field.
- Invariant to test: password change must verify the old password of exactly the authenticated account
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test changing another user's password from a view-role session
