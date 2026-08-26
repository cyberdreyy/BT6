# Q0227: identifier normalization mismatch in http_trigger_handler.NewHTTPTriggerHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit workflow identifiers at HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger) whose normalization in `NewHTTPTriggerHandler` differs from the form used for authorization or accounting, so one identity authorizes and another executes?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go](core/services/gateway/handlers/capabilities/v2/http_trigger_handler.go) -> `NewHTTPTriggerHandler`
- Entrypoint: HandleUserTriggerRequest on the public gateway endpoint (workflow HTTP trigger)
- Attacker controls: hex casing, length and padding of identifier fields (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `hex casing, length and padding of identifier fields` in mixed-case/0x-less/padded hex.
- Invariant to test: the canonical form must be computed once and reused for both decisions
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test asserting one canonical form is used for auth and execution
