# Q5485: token claims trusted without verification in user_controller.UpdateRole

## Question
Does the identity token processed by `UpdateRole` at /v2/users and /v2/user/* (password change, API token create/delete) get accepted with unverified signature, issuer, audience or expiry, letting an authenticated node user holding only the 'view' role present a self-issued token and become an admin?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `UpdateRole`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: target email in the path/body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `target email in the path/body` signed by an attacker key or with alg/kid manipulated.
- Invariant to test: identity tokens must be verified against the configured issuer keys, audience and expiry
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test presenting self-signed and expired tokens
