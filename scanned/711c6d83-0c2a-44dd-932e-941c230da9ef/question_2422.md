# Q2422: object identifier not ownership-scoped in vault_controller.ExportDKGResult

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) pass an identifier at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export that makes `ExportDKGResult` operate on an object outside their scope (another job, key, bridge, initiator, run)?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `ExportDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: dealer/recipient key identifiers (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `dealer/recipient key identifiers` referencing an object created by someone else.
- Invariant to test: handlers must scope lookups by the authenticated identity's entitlement
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test using foreign identifiers and asserting rejection
