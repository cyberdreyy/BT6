# Q4203: read route exposes a write-only field in csa_keys_controller.Create

## Question
Does the read path through `Create` at /v2/keys/csa and /v2/keys/csa/export/:ID return a field intended to be write-only (token, password, secret, private URL) to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/csa_keys_controller.go](core/web/csa_keys_controller.go) -> `Create`
- Entrypoint: /v2/keys/csa and /v2/keys/csa/export/:ID
- Attacker controls: imported key material (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `imported key material` after creating the object.
- Invariant to test: write-only fields must never be readable
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting write-only fields are absent from reads
