# Q0307: caip2 prefix matching is loose in poll.ts

## Question
caip2ToChainType matches on 'eip155:', 'solana:', 'bip122:' and 'tron:' prefixes only; can an attacker pass a caip2 string through poll: swallows operation errors whose prefix matches one chain family while the numeric reference points at another chain?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Pass 'eip155:999999' and observe the chain type and address chosen.
- Invariant to test: Chain identity must be resolved from the full caip2 reference, not the prefix.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: table-test caip2 strings through poll: swallows operation errors and assert full-reference validation.
