# Q2488: secret returned in the success response in evm_transfer_controller.CreateWithRelayer

## Question
Does the response produced by `CreateWithRelayer` at POST /v2/transfers/evm include key material, export bundles, passwords, tokens or bridge/EI secrets readable by an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateWithRelayer`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: from/to addresses (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `from/to addresses` and inspect every field of the response.
- Invariant to test: responses must never carry secret material to a non-owner or low-role caller
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the response body matches a redacted golden fixture
