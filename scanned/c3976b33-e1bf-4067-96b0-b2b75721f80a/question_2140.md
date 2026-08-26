# Q2140: DKG/vault result accepted without proof in vault_controller.VerifyDKGResult

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) submit or export a DKG/vault artifact through `VerifyDKGResult` at POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export without proving entitlement to the dealer/recipient identity, obtaining or corrupting secret shares?

## Target
- File/function: [core/web/vault_controller.go](core/web/vault_controller.go) -> `VerifyDKGResult`
- Entrypoint: POST /v2/vault/dkg_results/verify and /v2/vault/dkg_results/export
- Attacker controls: the DKG result payload (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `DKG result payload` naming another recipient/dealer.
- Invariant to test: DKG artifacts must be bound to the caller's proven key identity and admin authority
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: handler test exporting a foreign recipient's material
