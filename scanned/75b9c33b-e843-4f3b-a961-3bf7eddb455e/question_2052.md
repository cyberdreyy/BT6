# Q2052: clock/expiry comparison inverted in user_controller.Index

## Question
Is the expiry comparison in `Index` inverted or evaluated against the wrong field, so an expired session or token presented at /v2/users and /v2/user/* (password change, API token create/delete) by an authenticated node user holding only the 'view' role still authenticates?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Index`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: role value in the request (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `role value in the request` whose timestamps straddle the boundary.
- Invariant to test: expired credentials must be rejected at the exact boundary
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test at expiry-1/expiry/expiry+1
