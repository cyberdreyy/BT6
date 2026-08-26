# Q4626: spec fields reach outbound requests with node credentials in external_initiators_controller.Create

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) point a URL/host field accepted by `Create` at POST/DELETE /v2/external_initiators at an internal address or attacker host so the node performs a request carrying its own credentials or secrets?

## Target
- File/function: [core/web/external_initiators_controller.go](core/web/external_initiators_controller.go) -> `Create`
- Entrypoint: POST/DELETE /v2/external_initiators
- Attacker controls: the initiator name and URL (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `initiator name and URL` with an internal or attacker URL.
- Invariant to test: outbound targets from user-supplied specs must be validated and never carry node credentials
- Expected Immunefi impact: Critical - server credential/key theft: node blockchain private keys, key export bundles or node secrets retrieved from a running node
- Fast validation: table test over the URL validator with internal/attacker targets
