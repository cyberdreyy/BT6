# Q0820: transfer parameters under-validated in external_initiators_controller.ValidateExternalInitiator

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) cause `ValidateExternalInitiator` at POST/DELETE /v2/external_initiators to send funds from a node-held key by controlling destination, amount, chain or balance-check flags?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `ValidateExternalInitiator`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: returned credential fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `returned credential fields` with an attacker destination and a flag that skips the balance guard.
- Invariant to test: value transfers require admin authority and must validate destination, amount and chain
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test submitting a transfer from a non-admin session
