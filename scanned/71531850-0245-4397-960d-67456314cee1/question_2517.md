# Q2517: workflow ownership claimed, not proven in callback.Wait

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests name another owner's workflow in the fields validated by `Wait` at the callback used to return a DON response to the originating gateway user and have the DON execute it, with the work attributed to and paid by that owner?

## Target
- File/function: [core/services/gateway/handlers/common/callback.go](core/services/gateway/handlers/common/callback.go) -> `Wait`
- Entrypoint: the callback used to return a DON response to the originating gateway user
- Attacker controls: timing of late responses (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Submit `timing of late responses` with the victim's workflow owner/name/tag and the attacker's signature.
- Invariant to test: workflow execution must require proof that the sender is the owner or an authorized caller for that workflow
- Expected Immunefi impact: High - theft of protocol revenue: DON work charged to another owner/subscriber than the attacker
- Fast validation: table test submitting a trigger for a foreign workflow
