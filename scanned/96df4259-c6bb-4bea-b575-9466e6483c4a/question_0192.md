# Q0192: secret returned in the success response in csa_keys_controller.Index

## Question
Does the response produced by `Index` at /v2/keys/csa and /v2/keys/csa/export/:ID include key material, export bundles, passwords, tokens or bridge/EI secrets readable by an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Index`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: imported key material (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `imported key material` and inspect every field of the response.
- Invariant to test: responses must never carry secret material to a non-owner or low-role caller
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the response body matches a redacted golden fixture
