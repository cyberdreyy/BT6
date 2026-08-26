# Q2132: DKG/vault result accepted without proof in jobs_controller.Index

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) submit or export a DKG/vault artifact through `Index` at POST/PATCH /v2/jobs (edit role) without proving entitlement to the dealer/recipient identity, obtaining or corrupting secret shares?

## Target
- File/function: [core/web/jobs_controller.go](core/web/jobs_controller.go) -> `Index`
- Entrypoint: POST/PATCH /v2/jobs (edit role)
- Attacker controls: update payload on an existing job (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `update payload on an existing job` naming another recipient/dealer.
- Invariant to test: DKG artifacts must be bound to the caller's proven key identity and admin authority
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: handler test exporting a foreign recipient's material
