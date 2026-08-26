# Q0346: import path plants attacker key material in external_initiators_controller.ValidateExternalInitiator

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) import key material through `ValidateExternalInitiator` at POST/DELETE /v2/external_initiators so the node later signs oracle reports or transactions with an attacker-known key?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `ValidateExternalInitiator`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: returned credential fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `returned credential fields` containing a key the attacker generated.
- Invariant to test: key import must be admin-only and validated
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test importing a key from a non-admin session
