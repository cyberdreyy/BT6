# Q5936: session id echoed to the client in user_controller.UpdateRole

## Question
Is the session id or token echoed in a response body, header or log by `UpdateRole` at /v2/users and /v2/user/* (password change, API token create/delete) where an authenticated node user holding only the 'view' role or a lower-privileged viewer can read it?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `UpdateRole`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: target email in the path/body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger `target email in the path/body` and inspect all response surfaces.
- Invariant to test: session material must appear only in the Set-Cookie of its owner
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test scanning responses for session material
