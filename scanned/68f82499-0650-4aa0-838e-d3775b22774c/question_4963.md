# Q4963: role attribute taken from the request in user.ValidateAndHashPassword

## Question
Does the account/role creation path through `ValidateAndHashPassword` at POST /sessions and PATCH /v2/user/password accept the role from an unauthenticated HTTP client that can reach the node API port's payload rather than from server policy?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateAndHashPassword`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: email string (unicode, case, whitespace) (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Include `email string (unicode, case, whitespace)` with an elevated role field in the create/update body.
- Invariant to test: role assignment must be server-controlled and require admin authority
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: handler test posting a role field from a low-role session
