# Q2998: transfer parameters under-validated in csa_keys_controller.Create

## Question
Can an authenticated node user holding only the 'view' role cause `Create` at /v2/keys/csa and /v2/keys/csa/export/:ID to send funds from a node-held key by controlling destination, amount, chain or balance-check flags?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Create`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: the export password (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `export password` with an attacker destination and a flag that skips the balance guard.
- Invariant to test: value transfers require admin authority and must validate destination, amount and chain
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: handler test submitting a transfer from a non-admin session
