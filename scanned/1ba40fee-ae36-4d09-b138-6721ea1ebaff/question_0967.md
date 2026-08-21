# Q0967: completion decided by a status string in poll.ts

## Question
waitForCompletion polls until status !== 'executing' and reports success for any other value; can an attacker cause poll: swallows operation errors to report success for a failed, refunded or cancelled order?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Return a terminal status other than success and inspect the mapped result.
- Invariant to test: Only an explicit success status may be reported as success.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: enumerate terminal statuses through poll: swallows operation errors and assert only success maps to success.
