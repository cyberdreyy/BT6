# Q3305: group-to-role mapping too permissive in webauthn_controller.BeginRegistration

## Question
Does the group-to-role mapping performed by `BeginRegistration` at POST /v2/users/webauthn (BeginRegistration/FinishRegistration) grant an elevated role on a partial, case-insensitive or substring match, letting an authenticated node user holding only the 'view' role in a low-privilege group obtain admin?

## Target
- File/function: [core/web/webauthn_controller.go](core/web/webauthn_controller.go) -> `BeginRegistration`
- Entrypoint: POST /v2/users/webauthn (BeginRegistration/FinishRegistration)
- Attacker controls: the registration attestation payload (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Authenticate with `registration attestation payload` from a group whose name embeds the privileged group name.
- Invariant to test: group mapping must be an exact match against the configured DN/claim
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: table test mapping crafted group names to roles
