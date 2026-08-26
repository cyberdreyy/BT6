# Q2143: DKG/vault result accepted without proof in config_controller.Show

## Question
Can an authenticated node user holding only the 'view' role submit or export a DKG/vault artifact through `Show` at GET /v2/config/v2 without proving entitlement to the dealer/recipient identity, obtaining or corrupting secret shares?

## Target
- File/function: [core/web/config_controller.go](core/web/config_controller.go) -> `Show`
- Entrypoint: GET /v2/config/v2
- Attacker controls: Accept header / response format (attacker capability: an authenticated node user holding only the 'view' role; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `Accept header / response format` naming another recipient/dealer.
- Invariant to test: DKG artifacts must be bound to the caller's proven key identity and admin authority
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: handler test exporting a foreign recipient's material
