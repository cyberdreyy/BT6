# Q0527: slippage bps unbounded in poll.ts

## Question
generateDepositAddress passes slippage_bps straight through when provided; can an attacker set an extreme slippage through every deposit polling flow so the executed swap returns far less than the quote implied?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Submit a very large slippage value and inspect the quote body.
- Invariant to test: Slippage must be bounded and surfaced before the quote is created.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an out-of-range slippage to poll: swallows operation errors and assert clamping or rejection.
