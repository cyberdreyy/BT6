# Q1916: privileged default applied on missing field in keys_controller.Index

## Question
Does an omitted field in an authenticated node user holding only the 'view' role's request cause `Index` at /v2/keys/:keyType Index/Export/Import/Delete routes to apply a permissive default (all chains, no limit, enabled, admin) rather than rejecting?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Index`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the keyType path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Omit `keyType path parameter` from the request body.
- Invariant to test: missing security-relevant fields must be rejected, not defaulted permissively
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test omitting each security-relevant field
