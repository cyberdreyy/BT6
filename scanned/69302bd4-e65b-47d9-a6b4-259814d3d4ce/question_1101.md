# Q1101: request id mismatch tolerated in aggregator.methodSupportsSignedOCRValidation

## Question
Does `methodSupportsSignedOCRValidation` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user tolerate a mismatch between the id inside a signed payload and the id of the request being answered, letting any internet client with an arbitrary externally-owned key sending signed gateway requests splice a response from another request?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `methodSupportsSignedOCRValidation`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: method selection that toggles signed validation (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `method selection that toggles signed validation` whose ids differ.
- Invariant to test: the signed id must equal the served request id
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting mismatched ids are rejected
