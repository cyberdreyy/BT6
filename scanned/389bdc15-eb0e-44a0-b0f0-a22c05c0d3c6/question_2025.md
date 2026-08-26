# Q2025: grace window abused to alter the bundle in bundler.addError

## Question
Can any internet client with an arbitrary externally-owned key sending signed gateway requests submit during the grace window handled by `addError` at bundling of node responses returned to the requesting gateway user so the bundle returned to the user includes or omits responses chosen by the attacker?

## Target
- File/function: [core/services/gateway/handlers/confidentialrelay/bundler.go](core/services/gateway/handlers/confidentialrelay/bundler.go) -> `addError`
- Entrypoint: bundling of node responses returned to the requesting gateway user
- Attacker controls: repeat/duplicate requests (attacker capability: any internet client with an arbitrary externally-owned key sending signed gateway requests; no operator, admin, host, DB or DON-node privileges assumed)
- Exploit idea: Time `repeat/duplicate requests` against the grace deadline.
- Invariant to test: bundle composition must be determined by verified node responses only
- Expected Immunefi impact: Critical - misreporting of prices and/or data: attacker-controlled oracle job input/output reported on-chain
- Fast validation: table test over bundle composition across grace timings
