# Q5460: error response discloses secret material in response_cache.isExpiredOrNotCached

## Question
Do error paths in `isExpiredOrNotCached` at the gateway response cache serving repeated user trigger requests include node responses, partial plaintext or key identifiers that reveal secret material to any internet client with an arbitrary externally-owned key sending signed gateway requests?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/response_cache.go](core/services/gateway/handlers/capabilities/v2/response_cache.go) -> `isExpiredOrNotCached`
- Entrypoint: the gateway response cache serving repeated user trigger requests
- Attacker controls: status codes returned by the DON (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force partial failure with `status codes returned by the DON`.
- Invariant to test: error paths must not carry node payloads to the user
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting error payloads exclude node data
