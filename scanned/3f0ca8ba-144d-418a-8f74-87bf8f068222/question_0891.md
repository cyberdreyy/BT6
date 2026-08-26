# Q0891: MFA store cookie forgeable in user_controller.Index

## Question
Is the WebAuthn session-store cookie handled around `Index` unauthenticated or unsigned, letting an authenticated node user holding only the 'view' role craft one at /v2/users and /v2/user/* (password change, API token create/delete) to complete an MFA step for another user?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Index`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: API token access key (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Craft `API token access key` with attacker-chosen contents.
- Invariant to test: the MFA session store must be server-side or authenticated
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: unit test asserting a tampered store cookie is rejected
