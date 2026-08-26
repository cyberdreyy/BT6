# Q3276: signature validation optional by method in http_trigger_handler.HandleUserTriggerRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests select a method at HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger) for which `HandleUserTriggerRequest` skips signed-response validation, receiving an unverified or attacker-influenceable result?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go](core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go) -> `HandleUserTriggerRequest`
- Entrypoint: HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger)
- Attacker controls: workflowID, workflowOwner, workflowName, workflowTag fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Invoke `workflowID, workflowOwner, workflowName, workflowTag fields` on each advertised method.
- Invariant to test: signed validation must apply to every method returning sensitive or trusted data
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: matrix test asserting validation runs for every method
