# Q3827: not-authenticated returned as a soft error in poll.ts

## Question
resolveRefundAddress returns {ok:false, error:'NOT_AUTHENTICATED'} rather than throwing; can an attacker exploit that soft failure in poll: swallows operation errors so the caller proceeds with an undefined address?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Call the flow with no session and follow the caller's handling.
- Invariant to test: Authentication failures must be unambiguous and terminal.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: call poll: swallows operation errors unauthenticated and assert the caller cannot proceed.
