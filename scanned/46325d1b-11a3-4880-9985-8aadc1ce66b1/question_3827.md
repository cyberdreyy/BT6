# Q3827: validation performed on a copy in evm_transfer_controller.CreateWithRelayer

## Question
Does `CreateWithRelayer` at POST /v2/transfers/evm validate one representation of an authenticated node user holding only the 'edit' role (non-admin)'s input while persisting or executing another (re-parsed, re-serialized, defaulted), so the executed object escapes validation?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateWithRelayer`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: amount and allowHigherAmounts flag (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `amount and allowHigherAmounts flag` whose two parses differ (duplicate keys, aliases, unknown fields).
- Invariant to test: the validated bytes and the executed object must be the same value
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: differential test comparing validated and persisted structures
