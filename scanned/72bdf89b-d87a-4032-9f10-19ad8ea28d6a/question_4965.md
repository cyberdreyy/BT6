# Q4965: role attribute taken from the request in orm.FindUserByAPIToken

## Question
Does the account/role creation path through `FindUserByAPIToken` at POST /sessions, API-token auth headers and session cookie lookup accept the role from an unauthenticated HTTP client that can reach the node API port's payload rather than from server policy?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `FindUserByAPIToken`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: email casing/whitespace (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `email casing/whitespace` with an elevated role field in the create/update body.
- Invariant to test: role assignment must be server-controlled and require admin authority
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test posting a role field from a low-role session
