# Q2018: grace window abused to alter the bundle in handler.NewHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit during the grace window handled by `NewHandler` at web-API trigger and outgoing-request handling on the public gateway user endpoint so the bundle returned to the user includes or omits responses chosen by the attacker?

## Target
- File/function: [core/services/gateway/handlers/capabilities/handler.go](core/services/gateway/handlers/capabilities/handler.go) -> `NewHandler`
- Entrypoint: web-API trigger and outgoing-request handling on the public gateway user endpoint
- Attacker controls: messageId used for callback correlation (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `messageId used for callback correlation` against the grace deadline.
- Invariant to test: bundle composition must be determined by verified node responses only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test over bundle composition across grace timings
