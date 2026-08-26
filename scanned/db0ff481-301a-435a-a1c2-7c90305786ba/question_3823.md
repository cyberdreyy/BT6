# Q3823: validation performed on a copy in keys_controller.Create

## Question
Does `Create` at /v2/keys/:keyType Index/Export/Import/Delete routes validate one representation of an authenticated node user holding only the 'view' role's input while persisting or executing another (re-parsed, re-serialized, defaulted), so the executed object escapes validation?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Create`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the imported key JSON and its password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `imported key JSON and its password` whose two parses differ (duplicate keys, aliases, unknown fields).
- Invariant to test: the validated bytes and the executed object must be the same value
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: differential test comparing validated and persisted structures
