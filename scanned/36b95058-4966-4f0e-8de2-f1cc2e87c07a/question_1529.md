# Q1529: concurrent submissions break a uniqueness guard in dkg_recipient_keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role race two requests through `Index` at GET /v2/keys/dkgrecipient so a uniqueness or single-use guard is defeated (duplicate initiator, duplicate bridge, double run, double transfer)?

## Target
- File/function: [core/web/dkg_recipient_keys_controller.go](core/web/dkg_recipient_keys_controller.go) -> `Index`
- Entrypoint: GET /v2/keys/dkgrecipient
- Attacker controls: selected response fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fire concurrent `selected response fields`.
- Invariant to test: guards must be enforced by a transactional/unique constraint
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: concurrent handler test asserting exactly one success
