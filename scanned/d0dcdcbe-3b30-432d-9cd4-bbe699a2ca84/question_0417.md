# Q0417: stale session past expiry in user_controller.Index

## Question
Can an authenticated node user holding only the 'view' role keep a session alive indefinitely through the last-used update in `Index` at /v2/users and /v2/user/* (password change, API token create/delete), so a stolen or shared session never expires?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Index`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: target email in the path/body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Poll with `target email in the path/body` just under the reaper interval.
- Invariant to test: session lifetime must be bounded by absolute age, not only idle time
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: test advancing the clock past the absolute lifetime and asserting rejection
