# Q2021: grace window abused to alter the bundle in workflow_metadata_handler.NewWorkflowMetadataHandler

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit during the grace window handled by `NewWorkflowMetadataHandler` at the workflow metadata/authorization lookup consulted for every user trigger request so the bundle returned to the user includes or omits responses chosen by the attacker?

## Target
- File/function: [core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go](core/services/gateway/handlers/capabilities/v2/workflow_metadata_handler.go) -> `NewWorkflowMetadataHandler`
- Entrypoint: the workflow metadata/authorization lookup consulted for every user trigger request
- Attacker controls: authorization key material presented (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `authorization key material presented` against the grace deadline.
- Invariant to test: bundle composition must be determined by verified node responses only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test over bundle composition across grace timings
