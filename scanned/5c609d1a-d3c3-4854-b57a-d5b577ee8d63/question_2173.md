# Q2173: payload echoed unvalidated into the response in aggregator.methodSupportsSignedOCRValidation

## Question
Does `methodSupportsSignedOCRValidation` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user echo attacker-supplied request fields into the user response or into a signed artifact, letting any internet client with an arbitrary externally-owned key sending signed gateway requests shape data another party trusts?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `methodSupportsSignedOCRValidation`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: the request fields that derive the signed request id (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request fields that derive the signed request id` with crafted echoable fields.
- Invariant to test: responses must contain only server/DON-derived values
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test asserting no request field is echoed into signed output
