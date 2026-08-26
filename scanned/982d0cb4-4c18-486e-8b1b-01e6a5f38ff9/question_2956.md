# Q2956: cache poisoning of another user's result in http_trigger_handler.HandleUserTriggerRequest

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests write into the cache consulted by `HandleUserTriggerRequest` at HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger) so a later legitimate request receives attacker-controlled data used by a workflow?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go](core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go) -> `HandleUserTriggerRequest`
- Entrypoint: HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger)
- Attacker controls: trigger input payload and authorization key (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Prime the cache with `trigger input payload and authorization key`.
- Invariant to test: only DON-verified responses may populate the cache, keyed to their request
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: test priming and then asserting the victim's response origin
