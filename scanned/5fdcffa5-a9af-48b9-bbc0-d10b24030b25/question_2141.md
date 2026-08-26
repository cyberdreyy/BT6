# Q2141: DKG/vault result accepted without proof in evm_transfer_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) submit or export a DKG/vault artifact through `Create` at POST /v2/transfers/evm without proving entitlement to the dealer/recipient identity, obtaining or corrupting secret shares?

## Target
- File/function: [core/web/evm_transfer_controller.go](core/web/evm_transfer_controller.go) -> `Create`
- Entrypoint: POST /v2/transfers/evm
- Attacker controls: gas limit and token contract fields (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `gas limit and token contract fields` naming another recipient/dealer.
- Invariant to test: DKG artifacts must be bound to the caller's proven key identity and admin authority
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: handler test exporting a foreign recipient's material
