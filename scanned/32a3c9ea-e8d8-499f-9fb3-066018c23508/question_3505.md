# Q3505: identifier-to-object confusion across types in jobs_controller.Show

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) supply an identifier of the wrong type/namespace at POST/PATCH /v2/jobs (edit role) so `Show` resolves a different object class with weaker checks?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Show`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: the TOML job spec body (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `TOML job spec body` using another object's identifier format.
- Invariant to test: identifiers must be type- and namespace-checked before lookup
- Expected Immunefi impact: Critical - direct theft of funds: unauthorized transaction submission signed by node-held EVM keys
- Fast validation: table test passing cross-type identifiers
