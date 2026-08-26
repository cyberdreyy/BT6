# Q5833: DKG/vault result accepted without proof in keys_controller.Delete

## Question
Can an authenticated node user holding only the 'view' role submit or export a DKG/vault artifact through `Delete` at /v2/keys/:keyType Index/Export/Import/Delete routes without proving entitlement to the dealer/recipient identity, obtaining or corrupting secret shares?

## Target
- File/function: [core/web/keys_controller.go](core/web/keys_controller.go) -> `Delete`
- Entrypoint: /v2/keys/:keyType Index/Export/Import/Delete routes
- Attacker controls: the keyType path parameter (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `keyType path parameter` naming another recipient/dealer.
- Invariant to test: DKG artifacts must be bound to the caller's proven key identity and admin authority
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: handler test exporting a foreign recipient's material
