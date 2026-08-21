# Q0637: source and destination currency unchecked in poll.ts

## Question
The quote body accepts source_currency and destination_currency as opaque strings; can an attacker submit a pair through poll: swallows operation errors that the client never validates against getConfig, so the user approves a route they did not intend?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Submit currencies absent from the deposit config.
- Invariant to test: Quote parameters must be validated against the fetched deposit configuration.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit unsupported currencies to poll: swallows operation errors and assert client-side validation.
