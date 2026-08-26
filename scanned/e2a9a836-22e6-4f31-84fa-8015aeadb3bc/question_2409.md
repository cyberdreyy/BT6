# Q2409: identity provider failure fails open in user_controller.Index

## Question
If the external identity backend behind `Index` is unreachable, does /v2/users and /v2/user/* (password change, API token create/delete) fall back to a permissive path that authenticates an authenticated node user holding only the 'view' role or maps them to a default role?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Index`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: API token access key (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger the failure while submitting `API token access key`.
- Invariant to test: backend failure must fail closed with no role assignment
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test injecting backend errors and asserting a 401 with no session
