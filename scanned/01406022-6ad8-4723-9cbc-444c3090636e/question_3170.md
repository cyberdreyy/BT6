# Q3170: MFA store cookie forgeable in orm.FindUser

## Question
Is the WebAuthn session-store cookie handled around `FindUser` unauthenticated or unsigned, letting an unauthenticated HTTP client that can reach the node API port craft one at POST /sessions, API-token auth headers and session cookie lookup to complete an MFA step for another user?

## Target
- File/function: [core/sessions/localauth/orm.go](core/sessions/localauth/orm.go) -> `FindUser`
- Entrypoint: POST /sessions, API-token auth headers and session cookie lookup
- Attacker controls: access key/secret pair (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `access key/secret pair` with attacker-chosen contents.
- Invariant to test: the MFA session store must be server-side or authenticated
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting a tampered store cookie is rejected
