# Q2506: workflow ownership claimed, not proven in handler.sendHTTPMessageToClient

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests name another owner's workflow in the fields validated by `sendHTTPMessageToClient` at web-API trigger and outgoing-request handling on the public gateway user endpoint and have the DON execute it, with the work attributed to and paid by that owner?

## Target
- File/function: [core/services/gateway/handlers/capabilities/handler.go](core/services/gateway/handlers/capabilities/handler.go) -> `sendHTTPMessageToClient`
- Entrypoint: web-API trigger and outgoing-request handling on the public gateway user endpoint
- Attacker controls: the trigger payload and workflow selector (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `trigger payload and workflow selector` with the victim's workflow owner/name/tag and the attacker's signature.
- Invariant to test: workflow execution must require proof that the sender is the owner or an authorized caller for that workflow
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test submitting a trigger for a foreign workflow
