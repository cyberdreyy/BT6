# Q0161: role wrapper omitted on a route in auth.AuthenticateBySession

## Question
Is there a state-changing route reaching `AuthenticateBySession` from any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list that is registered without RequiresEditRole/RequiresAdminRole, letting a holder of a restricted API access-key/secret pair invoke it with only view or run rights?

## Target
- File/function: [core/web/auth/auth.go](core/web/auth/auth.go) -> `AuthenticateBySession`
- Entrypoint: any /v2 route wrapped by auth.Authenticate with the session/token/external-initiator authenticator list
- Attacker controls: external-initiator accessKey/secret headers (attacker capability: a holder of a restricted API access-key/secret pair; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Enumerate registered routes and compare each handler's declared minimum role against its wrapper, then call the weakest one with `external-initiator accessKey/secret headers`.
- Invariant to test: every state-changing /v2 route must be wrapped by the role gate matching its side effect
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: reflective route-table test asserting each non-GET route carries a role middleware
