# Q1407: abort signal supplied by the caller in poll.ts

## Question
poll checks a caller-supplied AbortSignal; can an attacker abort poll: swallows operation errors at a chosen moment so the app treats a completed deposit as aborted and issues a duplicate?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Abort right after the funds land.
- Invariant to test: Abort must not change the recorded outcome of a settled deposit.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: abort poll: swallows operation errors after settlement and assert the state reflects settlement.
