# Q1037: group-to-role mapping too permissive in session.NewSession

## Question
Does the group-to-role mapping performed by `NewSession` at POST /sessions (session creation) and API-token authentication grant an elevated role on a partial, case-insensitive or substring match, letting an unauthenticated HTTP client that can reach the node API port in a low-privilege group obtain admin?

## Target
- File/function: [core/sessions/session.go](core/sessions/session.go) -> `NewSession`
- Entrypoint: POST /sessions (session creation) and API-token authentication
- Attacker controls: WebAuthn data field (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Authenticate with `WebAuthn data field` from a group whose name embeds the privileged group name.
- Invariant to test: group mapping must be an exact match against the configured DN/claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test mapping crafted group names to roles
