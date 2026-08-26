# Q1328: error response discloses secret material in http_trigger_handler.NewHTTPTriggerHandler

## Question
Do error paths in `NewHTTPTriggerHandler` at HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger) include node responses, partial plaintext or key identifiers that reveal secret material to any internet client with an arbitrary externally-owned key sending signed gateway requests?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go](core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go) -> `NewHTTPTriggerHandler`
- Entrypoint: HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger)
- Attacker controls: workflowID, workflowOwner, workflowName, workflowTag fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Force partial failure with `workflowID, workflowOwner, workflowName, workflowTag fields`.
- Invariant to test: error paths must not carry node payloads to the user
- Expected Immunefi impact: Critical - server credential theft: vault/DKG secret shares or decrypted user secrets disclosed to an unauthorized requester
- Fast validation: table test asserting error payloads exclude node data
