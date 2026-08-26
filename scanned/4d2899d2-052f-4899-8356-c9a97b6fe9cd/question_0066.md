# Q0066: workflow ownership claimed, not proven in webapi.Validate

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests name another owner's workflow in the fields validated by `Validate` at the web-API capability handler config validation and request path from the public gateway endpoint and have the DON execute it, with the work attributed to and paid by that owner?

## Target
- File/function: [core/services/gateway/handlers/capabilities/webapi.go](core/services/gateway/handlers/capabilities/webapi.go) -> `Validate`
- Entrypoint: the web-API capability handler config validation and request path from the public gateway endpoint
- Attacker controls: the request URL, method and headers in the payload (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `request URL, method and headers in the payload` with the victim's workflow owner/name/tag and the attacker's signature.
- Invariant to test: workflow execution must require proof that the sender is the owner or an authorized caller for that workflow
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test submitting a trigger for a foreign workflow
