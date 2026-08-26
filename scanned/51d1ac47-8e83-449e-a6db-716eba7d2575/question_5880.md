# Q5880: token lookup ignores scope in user_controller.UpdateRole

## Question
Does the API token lookup performed by `UpdateRole` at /v2/users and /v2/user/* (password change, API token create/delete) return a user without checking the token's owner, expiry or state, letting an authenticated node user holding only the 'view' role present a deleted user's token?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `UpdateRole`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: oldPassword/newPassword fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `oldPassword/newPassword fields` belonging to a deleted or downgraded account.
- Invariant to test: token authentication must re-validate the owning account's existence and role
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: integration test using a token after its owner is deleted
