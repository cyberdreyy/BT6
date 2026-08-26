# Q0076: workflow ownership claimed, not proven in aggregator.methodSupportsSignedOCRValidation

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests name another owner's workflow in the fields validated by `methodSupportsSignedOCRValidation` at aggregation and signature/quorum validation of vault node responses before they reach the requesting user and have the DON execute it, with the work attributed to and paid by that owner?

## Target
- File/function: [core/services/gateway/handlers/vault/aggregator.go](core/services/gateway/handlers/vault/aggregator.go) -> `methodSupportsSignedOCRValidation`
- Entrypoint: aggregation and signature/quorum validation of vault node responses before they reach the requesting user
- Attacker controls: the request fields that derive the signed request id (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request fields that derive the signed request id` with the victim's workflow owner/name/tag and the attacker's signature.
- Invariant to test: workflow execution must require proof that the sender is the owner or an authorized caller for that workflow
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test submitting a trigger for a foreign workflow
