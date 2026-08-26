# Q3493: state parameter not verified in client.CreateEphemeralConnection

## Question
Is the state/nonce checked by `CreateEphemeralConnection` at POST /sessions against the configured LDAP server unbound to the initiating browser session, letting an unauthenticated HTTP client that can reach the node API port inject their own authorization code and take over the resulting node session?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `CreateEphemeralConnection`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: username and password fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Deliver `username and password fields` with an attacker-obtained code and a replayed state.
- Invariant to test: state must be single-use and bound to the initiating session
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test replaying state/code pairs across sessions
