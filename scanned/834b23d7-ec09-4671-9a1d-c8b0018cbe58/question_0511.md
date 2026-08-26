# Q0511: spec fields reach outbound requests with node credentials in vault_controller.VerifyDKGResult

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) point a URL/host field accepted by `VerifyDKGResult` at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export at an internal address or attacker host so the node performs a request carrying its own credentials or secrets?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `VerifyDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: the DKG result payload (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `DKG result payload` with an internal or attacker URL.
- Invariant to test: outbound targets from user-supplied specs must be validated and never carry node credentials
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the URL validator with internal/attacker targets
