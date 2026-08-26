# Q3345: request id mismatch tolerated in bundler.setSignedResponse

## Question
Does `setSignedResponse` at bundling of node responses returned to the requesting gateway user tolerate a mismatch between the id inside a signed payload and the id of the request being answered, letting any internet client with an arbitrary externally-owned key sending signed gateway requests splice a response from another request?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `setSignedResponse`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: the request that determines bundle composition (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request that determines bundle composition` whose ids differ.
- Invariant to test: the signed id must equal the served request id
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting mismatched ids are rejected
