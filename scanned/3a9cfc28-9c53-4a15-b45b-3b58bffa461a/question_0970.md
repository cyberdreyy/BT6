# Q0970: directory metacharacter injection in identity lookup in user_controller.Index

## Question
Can an authenticated node user holding only the 'view' role inject filter metacharacters through `Index` at /v2/users and /v2/user/* (password change, API token create/delete) so the identity query matches an administrator entry instead of the submitted account?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Index`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: oldPassword/newPassword fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `oldPassword/newPassword fields` containing filter/DN metacharacters.
- Invariant to test: all externally supplied values must be escaped before entering the identity query
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test over the query builder with metacharacter payloads
