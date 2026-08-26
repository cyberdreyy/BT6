# Q0131: initiator not bound to its job in orm.NewORM

## Question
Can an authenticated node user holding only the 'edit' role (non-admin) authenticate with one initiator's credential at bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs and, through `NewORM`, trigger runs for jobs bound to a different initiator?

## Target
- File/function: [core/bridges/orm.go](core/bridges/orm.go) -> `NewORM`
- Entrypoint: bridge and external-initiator persistence reached from /v2/bridge_types, /v2/external_initiators and job runs
- Attacker controls: external initiator name (attacker capability: an authenticated node user holding only the 'edit' role (non-admin); no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Present `external initiator name` against another job's run endpoint.
- Invariant to test: an initiator may only trigger the jobs whose spec names it
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: handler test triggering a foreign job with a valid EI credential
