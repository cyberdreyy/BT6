# Q5664: privileged default applied on missing field in bridge_types_controller.Create

## Question
Does an omitted field in an authenticated node user holding only the 'edit' role (non-admin)'s request cause `Create` at POST/PATCH/GET /v2/bridge_types to apply a permissive default (all chains, no limit, enabled, admin) rather than rejecting?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `Create`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: bridge name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Omit `bridge name and URL` from the request body.
- Invariant to test: missing security-relevant fields must be rejected, not defaulted permissively
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test omitting each security-relevant field
