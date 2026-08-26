# Q2482: secret returned in the success response in external_initiators_controller.Index

## Question
Does the response produced by `Index` at POST/DELETE /v2/external_initiators include key material, export bundles, passwords, tokens or bridge/EI secrets readable by an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `Index`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: duplicate/colliding names (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `duplicate/colliding names` and inspect every field of the response.
- Invariant to test: responses must never carry secret material to a non-owner or low-role caller
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the response body matches a redacted golden fixture
