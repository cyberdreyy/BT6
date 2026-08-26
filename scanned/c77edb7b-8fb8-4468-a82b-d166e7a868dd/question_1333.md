# Q1333: error response discloses secret material in bundler.addError

## Question
Do error paths in `addError` at bundling of node responses returned to the requesting gateway user include node responses, partial plaintext or key identifiers that reveal secret material to any internet client with an arbitrary externally-owned key sending signed gateway requests?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `addError`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: repeat/duplicate requests (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force partial failure with `repeat/duplicate requests`.
- Invariant to test: error paths must not carry node payloads to the user
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting error payloads exclude node data
