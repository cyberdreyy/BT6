# Q0180: session token generation entropy in user_controller.Index

## Question
Is the session id or API token produced on the path through `Index` derived from a predictable source (time, counter, weak RNG), letting an authenticated node user holding only the 'view' role predict a token issued to an admin and replay it at /v2/users and /v2/user/* (password change, API token create/delete)?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Index`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: role value in the request (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Collect many issued values via `role value in the request` and test for structure.
- Invariant to test: session ids and API tokens must come from a CSPRNG with full entropy
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: statistical test over many generated tokens plus a code path review of the RNG source
