# Q5968: grace window abused to alter the bundle in handler.armGraceDeadline

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit during the grace window handled by `armGraceDeadline` at HandleJSONRPCUserMessage on the confidential-relay gateway method so the bundle returned to the user includes or omits responses chosen by the attacker?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/handler.go](core/services/gateway/handlers/confidentialrelay/handler.go) -> `armGraceDeadline`
- Entrypoint: HandleJSONRPCUserMessage on the confidential-relay gateway method
- Attacker controls: submission timing relative to the quorum grace window (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `submission timing relative to the quorum grace window` against the grace deadline.
- Invariant to test: bundle composition must be determined by verified node responses only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test over bundle composition across grace timings
