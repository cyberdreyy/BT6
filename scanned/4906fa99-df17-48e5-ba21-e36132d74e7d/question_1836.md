# Q1836: validation performed on a copy in external_initiators_controller.ValidateExternalInitiator

## Question
Does `ValidateExternalInitiator` at POST/DELETE /v2/external_initiators validate one representation of an authenticated node user holding only the 'edit' role (non-admin)'s input while persisting or executing another (re-parsed, re-serialized, defaulted), so the executed object escapes validation?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `ValidateExternalInitiator`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: duplicate/colliding names (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `duplicate/colliding names` whose two parses differ (duplicate keys, aliases, unknown fields).
- Invariant to test: the validated bytes and the executed object must be the same value
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: differential test comparing validated and persisted structures
