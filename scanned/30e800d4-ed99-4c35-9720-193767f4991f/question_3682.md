# Q3682: redirect target attacker-controlled in client.CreateEphemeralConnection

## Question
Can an unauthenticated HTTP client that can reach the node API port steer the post-authentication redirect handled near `CreateEphemeralConnection` at POST /sessions against the configured LDAP server to an external host, capturing the issued session cookie or code?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `CreateEphemeralConnection`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: username and password fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Supply `username and password fields` with an absolute or protocol-relative URL.
- Invariant to test: redirect targets must be restricted to a server-side allowlist
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the redirect validator with hostile URLs
