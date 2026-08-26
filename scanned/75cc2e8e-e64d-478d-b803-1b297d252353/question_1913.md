# Q1913: privileged default applied on missing field in jobs_controller.Index

## Question
Does an omitted field in an authenticated node user holding only the 'edit' role (non-admin)'s request cause `Index` at POST/PATCH /v2/jobs (edit role) to apply a permissive default (all chains, no limit, enabled, admin) rather than rejecting?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Index`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: the TOML job spec body (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Omit `TOML job spec body` from the request body.
- Invariant to test: missing security-relevant fields must be rejected, not defaulted permissively
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test omitting each security-relevant field
