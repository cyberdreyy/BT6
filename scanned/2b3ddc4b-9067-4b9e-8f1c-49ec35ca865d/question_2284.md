# Q2284: read route exposes a write-only field in workflow_keys_controller.Index

## Question
Does the read path through `Index` at GET /v2/keys/workflow return a field intended to be write-only (token, password, secret, private URL) to an authenticated node user holding only the 'view' role?

## Target
- File/function: [core/web/workflow_keys_controller.go](core/web/workflow_keys_controller.go) -> `Index`
- Entrypoint: GET /v2/keys/workflow
- Attacker controls: selected response fields (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `selected response fields` after creating the object.
- Invariant to test: write-only fields must never be readable
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting write-only fields are absent from reads
