# Q3717: onWalletCreated callback fires before confirmation in poll.ts

## Question
resolveRefundAddress invokes onWalletCreated after the create call returns; can an attacker use poll: swallows operation errors so the app treats an unconfirmed wallet as ready and routes funds to it?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Return a create response and inspect the callback timing versus session refresh.
- Invariant to test: Callbacks signalling readiness must follow a confirmed session refresh.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert poll: swallows operation errors refreshes the user before invoking the callback.
