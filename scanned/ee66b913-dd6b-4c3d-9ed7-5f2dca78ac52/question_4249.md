# Q4249: session store keyed on user input in client.CreateEphemeralConnection

## Question
Is any session/MFA store keyed by a value an unauthenticated HTTP client that can reach the node API port supplies at POST /sessions against the configured LDAP server on the path through `CreateEphemeralConnection`, allowing collision with another user's entry?

## Target
- File/function: [core/sessions/ldapauth/client.go](core/sessions/ldapauth/client.go) -> `CreateEphemeralConnection`
- Entrypoint: POST /sessions against the configured LDAP server
- Attacker controls: username and password fields (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `username and password fields` chosen to collide with an operator's key.
- Invariant to test: server-side session state must be keyed by an unguessable server-generated id
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting store keys are server-generated
