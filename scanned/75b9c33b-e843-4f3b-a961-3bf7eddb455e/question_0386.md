# Q0386: request id derivation collides in workflow_metadata_handler.NewWorkflowMetadataHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests construct two distinct requests at the workflow metadata/authorization lookup consulted for every user trigger request that derive the same request id in `NewWorkflowMetadataHandler`, so one user's response is delivered to the other?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `NewWorkflowMetadataHandler`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: authorization key material presented (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `authorization key material presented` varying a field excluded from the derivation.
- Invariant to test: the request id must be a collision-resistant function of every authorization-relevant field including the sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting distinct requests always derive distinct ids
