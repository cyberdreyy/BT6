# Q2238: method routing selects a weaker handler in http_trigger_handler.NewHTTPTriggerHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests name a method at HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger) that `NewHTTPTriggerHandler` routes to a handler with weaker authorization while the payload targets a privileged capability?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go](core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go) -> `NewHTTPTriggerHandler`
- Entrypoint: HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger)
- Attacker controls: workflowID, workflowOwner, workflowName, workflowTag fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `workflowID, workflowOwner, workflowName, workflowTag fields` with a mismatched method/payload pair.
- Invariant to test: method routing and payload authorization must be consistent
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: matrix test over method/payload mismatches
