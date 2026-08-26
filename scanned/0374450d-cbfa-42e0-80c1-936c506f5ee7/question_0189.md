# Q0189: secret returned in the success response in bridge_types_controller.ValidateBridgeTypeNotExist

## Question
Does the response produced by `ValidateBridgeTypeNotExist` at POST/PATCH/GET /v2/bridge_types include key material, export bundles, passwords, tokens or bridge/EI secrets readable by an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeTypeNotExist`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: confirmations and minimum contract payment (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `confirmations and minimum contract payment` and inspect every field of the response.
- Invariant to test: responses must never carry secret material to a non-owner or low-role caller
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting the response body matches a redacted golden fixture
