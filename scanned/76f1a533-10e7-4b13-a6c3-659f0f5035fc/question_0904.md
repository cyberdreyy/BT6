# Q0904: balance/limit guard bypass in dkg_recipient_keys_controller.Index

## Question
Can an authenticated node user holding only the 'view' role bypass the balance or limit validation performed by `Index` at GET /v2/keys/dkgrecipient through numeric parsing (overflow, negative, scientific notation, unit confusion)?

## Target
- File/function: [core/web/dkg_recipient_keys_controller.go](core/web/dkg_recipient_keys_controller.go) -> `Index`
- Entrypoint: GET /v2/keys/dkgrecipient
- Attacker controls: selected response fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `selected response fields` in hostile numeric encodings.
- Invariant to test: amount validation must be exact over the canonical big-int representation
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test over the amount parser and balance validator
