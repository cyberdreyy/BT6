# Q2764: request id derivation collides in http_trigger_handler.HandleUserTriggerRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests construct two distinct requests at HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger) that derive the same request id in `HandleUserTriggerRequest`, so one user's response is delivered to the other?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go](core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go) -> `HandleUserTriggerRequest`
- Entrypoint: HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger)
- Attacker controls: workflowID, workflowOwner, workflowName, workflowTag fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `workflowID, workflowOwner, workflowName, workflowTag fields` varying a field excluded from the derivation.
- Invariant to test: the request id must be a collision-resistant function of every authorization-relevant field including the sender
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: test asserting distinct requests always derive distinct ids
