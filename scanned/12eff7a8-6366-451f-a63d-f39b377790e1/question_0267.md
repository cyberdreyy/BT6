# Q0267: export password not enforced in external_initiators_controller.ValidateExternalInitiator

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) export key material through `ValidateExternalInitiator` at POST/DELETE /v2/external_initiators with an empty, default or attacker-chosen password, obtaining a bundle that is trivially decryptable offline?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `ValidateExternalInitiator`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: the initiator name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Call `initiator name and URL` with an empty/weak password parameter.
- Invariant to test: export must require the caller's authenticated proof and a strong password
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: handler test exporting with an empty password and asserting failure
