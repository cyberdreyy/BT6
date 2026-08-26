# Q2279: read route exposes a write-only field in bridge_types_controller.ValidateBridgeTypeNotExist

## Question
Does the read path through `ValidateBridgeTypeNotExist` at POST/PATCH/GET /v2/bridge_types return a field intended to be write-only (token, password, secret, private URL) to an authenticated node user holding only the 'edit' role (non-admin)?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `ValidateBridgeTypeNotExist`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: confirmations and minimum contract payment (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Fetch `confirmations and minimum contract payment` after creating the object.
- Invariant to test: write-only fields must never be readable
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test asserting write-only fields are absent from reads
