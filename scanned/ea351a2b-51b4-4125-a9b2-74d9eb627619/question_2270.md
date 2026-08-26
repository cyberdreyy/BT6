# Q2270: error path leaves partial authentication in user_controller.Index

## Question
Does a failure after partial authentication in `Index` at /v2/users and /v2/user/* (password change, API token create/delete) still persist a session row or set a cookie usable by an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Index`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: target email in the path/body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force the late failure using `target email in the path/body`.
- Invariant to test: no session artifact may survive a failed authentication
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test asserting no session row/cookie after each failure branch
