# Q1924: privileged default applied on missing field in config_controller.Show

## Question
Does an omitted field in an authenticated node user holding only the 'view' role's request cause `Show` at GET /v2/config/v2 to apply a permissive default (all chains, no limit, enabled, admin) rather than rejecting?

## Target
- File/function: [core/web/config_controller.go](core/web/config_controller.go) -> `Show`
- Entrypoint: GET /v2/config/v2
- Attacker controls: the request path and query parameters (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Omit `request path and query parameters` from the request body.
- Invariant to test: missing security-relevant fields must be rejected, not defaulted permissively
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test omitting each security-relevant field
