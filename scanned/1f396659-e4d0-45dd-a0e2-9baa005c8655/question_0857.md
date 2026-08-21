# Q0857: quoteCreatedAt is a client cursor in poll.ts

## Question
The `after` query is the caller's quoteCreatedAt; can an attacker pass a cursor through poll: swallows operation errors that surfaces an older or unrelated order as the user's deposit?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Pass a much earlier cursor and observe the order returned.
- Invariant to test: The polling cursor must be server-issued and bound to the quote.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass a stale cursor to poll: swallows operation errors and assert it is refused.
