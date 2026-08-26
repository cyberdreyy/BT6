# Q5832: DKG/vault result accepted without proof in bridge_types_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) submit or export a DKG/vault artifact through `Create` at POST/PATCH/GET /v2/bridge_types without proving entitlement to the dealer/recipient identity, obtaining or corrupting secret shares?

## Target
- File/function: [core/web/bridge_types_controller.go](core/web/bridge_types_controller.go) -> `Create`
- Entrypoint: POST/PATCH/GET /v2/bridge_types
- Attacker controls: bridge name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `bridge name and URL` naming another recipient/dealer.
- Invariant to test: DKG artifacts must be bound to the caller's proven key identity and admin authority
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: handler test exporting a foreign recipient's material
