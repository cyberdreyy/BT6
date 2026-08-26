# Q5423: state parameter not verified in ldap.FindUserByAPIToken

## Question
Is the state/nonce checked by `FindUserByAPIToken` at POST /sessions when the LDAP authentication provider is configured unbound to the initiating browser session, letting an unauthenticated HTTP client that can reach the node API port inject their own authorization code and take over the resulting node session?

## Target
- File/function: [core/sessions/ldapauth/ldap.go](core/sessions/ldapauth/ldap.go) -> `FindUserByAPIToken`
- Entrypoint: POST /sessions when the LDAP authentication provider is configured
- Attacker controls: email/username string (LDAP metacharacters) (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Deliver `email/username string (LDAP metacharacters)` with an attacker-obtained code and a replayed state.
- Invariant to test: state must be single-use and bound to the initiating session
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test replaying state/code pairs across sessions
