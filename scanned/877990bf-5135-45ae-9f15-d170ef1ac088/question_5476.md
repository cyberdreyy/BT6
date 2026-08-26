# Q5476: token claims trusted without verification in user.ValidateAndHashPassword

## Question
Does the identity token processed by `ValidateAndHashPassword` at POST /sessions and PATCH /v2/user/password get accepted with unverified signature, issuer, audience or expiry, letting an unauthenticated HTTP client that can reach the node API port present a self-issued token and become an admin?

## Target
- File/function: [core/sessions/user.go](core/sessions/user.go) -> `ValidateAndHashPassword`
- Entrypoint: POST /sessions and PATCH /v2/user/password
- Attacker controls: email string (unicode, case, whitespace) (attacker capability: an unauthenticated HTTP client that can reach the node API port; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `email string (unicode, case, whitespace)` signed by an attacker key or with alg/kid manipulated.
- Invariant to test: identity tokens must be verified against the configured issuer keys, audience and expiry
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test presenting self-signed and expired tokens
