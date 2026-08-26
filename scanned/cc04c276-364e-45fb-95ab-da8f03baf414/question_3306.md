# Q3306: group-to-role mapping too permissive in user_controller.Create

## Question
Does the group-to-role mapping performed by `Create` at /v2/users and /v2/user/* (password change, API token create/delete) grant an elevated role on a partial, case-insensitive or substring match, letting an authenticated node user holding only the 'view' role in a low-privilege group obtain admin?

## Target
- File/function: [core/web/user_controller.go](core/web/user_controller.go) -> `Create`
- Entrypoint: /v2/users and /v2/user/* (password change, API token create/delete)
- Attacker controls: target email in the path/body (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Authenticate with `target email in the path/body` from a group whose name embeds the privileged group name.
- Invariant to test: group mapping must be an exact match against the configured DN/claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test mapping crafted group names to roles
