# Q0985: state change without authorization ordering in vault_controller.VerifyDKGResult

## Question
Does `VerifyDKGResult` at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export mutate state before completing its authorization or validation, so an authenticated node user holding only the 'edit' role (non-admin) gets the effect together with the error?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `VerifyDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: the DKG result payload (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `DKG result payload` that fails late.
- Invariant to test: no state change may precede a completed authorization
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test asserting no mutation accompanies an error response
