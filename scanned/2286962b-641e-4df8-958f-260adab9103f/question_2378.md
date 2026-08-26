# Q2378: expired entry cleanup races delivery in workflow_metadata_handler.NewWorkflowMetadataHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests time a request at the workflow metadata/authorization lookup consulted for every user trigger request so cleanup in `NewWorkflowMetadataHandler` removes an entry mid-delivery and a later response is matched to the attacker's new request?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `NewWorkflowMetadataHandler`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: workflow owner/name/tag claimed in the request (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `workflow owner/name/tag claimed in the request` against the expiry sweep.
- Invariant to test: cleanup and delivery must be mutually exclusive per entry
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: concurrency test racing cleanup against delivery
