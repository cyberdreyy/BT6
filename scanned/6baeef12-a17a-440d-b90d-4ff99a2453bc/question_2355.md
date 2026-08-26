# Q2355: route role weaker than the side effect in vault_controller.ExportDKGResult

## Question
Is the route reaching `ExportDKGResult` at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export gated by a role weaker than the effect it produces, letting an authenticated node user holding only the 'edit' role (non-admin) cause it?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `ExportDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: the DKG result payload (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `DKG result payload` from the weakest session the route accepts.
- Invariant to test: the route gate must match the strongest side effect of the handler
- Expected Immunefi impact: Critical - node takeover: an unauthenticated or low-role attacker gains admin control of the node, enabling key export and unauthorized transaction submission
- Fast validation: route test invoking the handler at each role level
