# Q2020: grace window abused to alter the bundle in http_trigger_handler.NewHTTPTriggerHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit during the grace window handled by `NewHTTPTriggerHandler` at HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger) so the bundle returned to the user includes or omits responses chosen by the attacker?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go](core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go) -> `NewHTTPTriggerHandler`
- Entrypoint: HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger)
- Attacker controls: requestID/key and its derivation inputs (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `requestID/key and its derivation inputs` against the grace deadline.
- Invariant to test: bundle composition must be determined by verified node responses only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test over bundle composition across grace timings
