# Q3953: error text discloses key or file paths in evm_transfer_controller.CreateWithRelayer

## Question
Do errors from `CreateWithRelayer` at POST /v2/transfers/evm reveal keystore paths, key ids, addresses or DB structure that let an authenticated node user holding only the 'edit' role (non-admin) target the next step of a key-theft chain?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `CreateWithRelayer`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: gas limit and token contract fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force errors with `gas limit and token contract fields`.
- Invariant to test: errors must not disclose key identities or filesystem layout
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting error bodies exclude paths and key ids
