# Q1843: validation performed on a copy in vault_controller.VerifyDKGResult

## Question
Does `VerifyDKGResult` at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export validate one representation of an authenticated node user holding only the 'edit' role (non-admin)'s input while persisting or executing another (re-parsed, re-serialized, defaulted), so the executed object escapes validation?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `VerifyDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: the export request parameters (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `export request parameters` whose two parses differ (duplicate keys, aliases, unknown fields).
- Invariant to test: the validated bytes and the executed object must be the same value
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: differential test comparing validated and persisted structures
