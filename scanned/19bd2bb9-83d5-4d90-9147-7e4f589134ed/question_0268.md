# Q0268: export password not enforced in bridge_types_controller.ValidateBridgeTypeNotExist

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) export key material through `ValidateBridgeTypeNotExist` at POST/PATCH/GET /v2/bridge_types with an empty, default or attacker-chosen password, obtaining a bundle that is trivially decryptable offline?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeTypeNotExist`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: bridge name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Call `bridge name and URL` with an empty/weak password parameter.
- Invariant to test: export must require the caller's authenticated proof and a strong password
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test exporting with an empty password and asserting failure
