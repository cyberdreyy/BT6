# Q5948: read route exposes a write-only field in evm_transfer_controller.CreateEVMLegacy

## Question
Does the read path through `CreateEVMLegacy` at POST /v2/transfers/evm return a field intended to be write-only (token, password, secret, private URL) to an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateEVMLegacy`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: amount and allowHigherAmounts flag (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `amount and allowHigherAmounts flag` after creating the object.
- Invariant to test: write-only fields must never be readable
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting write-only fields are absent from reads
