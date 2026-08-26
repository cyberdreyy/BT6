# Q2098: undecodable responses counted as valid in bundler.addError

## Question
Does `addError` at bundling of node responses returned to the requesting gateway user count undecodable or error responses toward success, letting any internet client with an arbitrary externally-owned key sending signed gateway requests force a result with fewer honest contributions?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `addError`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: fields echoed back into the bundle (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Trigger the mixed-response branch with `fields echoed back into the bundle`.
- Invariant to test: only successfully decoded, verified responses may count
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test with mixed decodable/undecodable responses
