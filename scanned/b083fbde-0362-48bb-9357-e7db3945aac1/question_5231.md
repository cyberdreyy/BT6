# Q5231: signature validation optional by method in workflow_metadata_handler.syncMetadata

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests select a method at the workflow metadata/authorization lookup consulted for every user trigger request for which `syncMetadata` skips signed-response validation, receiving an unverified or attacker-influenceable result?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `syncMetadata`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: authorization key material presented (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `authorization key material presented` on each advertised method.
- Invariant to test: signed validation must apply to every method returning sensitive or trusted data
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: matrix test asserting validation runs for every method
