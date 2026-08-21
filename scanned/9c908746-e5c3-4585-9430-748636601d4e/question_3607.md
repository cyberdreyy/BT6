# Q3607: order lookup by id alone in poll.ts

## Question
getDeposit fetches an order purely by order id; can an attacker call poll: swallows operation errors with another user's order id and read the deposit details?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Call the order read with a foreign id.
- Invariant to test: Order reads must be scoped to the authenticated user.
- Expected Immunefi impact: Critical - retrieval of sensitive user data: session/identity/provider tokens, key-material handles, or entropy identifiers reach a party that must not hold them.
- Fast validation: Integration test: read a foreign order through poll: swallows operation errors and assert refusal.
