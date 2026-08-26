# Q3475: secret identifier traversal in aggregator.signedResponseRequestIDEnabled

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests address another namespace or owner through identifier separators/encoding in the request validated by `signedResponseRequestIDEnabled` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `signedResponseRequestIDEnabled`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: repeat requests with mutated identifiers (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `repeat requests with mutated identifiers` with separators, encoded delimiters or empty components.
- Invariant to test: identifier components must be validated and joined unambiguously
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test over identifier parsing with hostile components
