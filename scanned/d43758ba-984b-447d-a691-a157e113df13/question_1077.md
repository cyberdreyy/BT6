# Q1077: timeout mapped to the same shape as success in poll.ts

## Question
The poll result mapper turns success-with-no-result into {status:'timeout'} and errors into timeouts too; can an attacker exploit that collapse through poll: swallows operation errors so a failed deposit is presented as merely slow and the user re-sends funds?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Force error and timeout paths and compare what the caller sees.
- Invariant to test: Failure and timeout must be distinguishable to the caller.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: force each path in poll: swallows operation errors and assert distinct result shapes.
