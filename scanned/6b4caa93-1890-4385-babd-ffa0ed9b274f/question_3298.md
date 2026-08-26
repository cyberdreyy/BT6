# Q3298: group-to-role mapping too permissive in orm.FindUser

## Question
Does the group-to-role mapping performed by `FindUser` at POST /sessions, API-token auth headers and session cookie lookup grant an elevated role on a partial, case-insensitive or substring match, letting an unauthenticated HTTP client that can reach the node API port in a low-privilege group obtain admin?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `FindUser`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: password bytes (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Authenticate with `password bytes` from a group whose name embeds the privileged group name.
- Invariant to test: group mapping must be an exact match against the configured DN/claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test mapping crafted group names to roles
