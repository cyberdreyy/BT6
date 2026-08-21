# Q3387: funding api selects the provider by property in poll.ts

## Question
FundingApi exposes moonpay and coinbase; can an attacker cause poll: swallows operation errors to route a funding request to a provider the app did not configure, with parameters shaped for the other?

## Target
- File/function: [src/utils/poll.ts](src/utils/poll.ts) - poll: swallows operation errors, returns {status:'max_attempts'|'aborted'|'success'}
- Entrypoint: every deposit polling flow
- Attacker controls: operation results and errors, abort timing, attempt arithmetic
- Exploit idea: Call each provider with the other's parameter shape.
- Invariant to test: Provider selection and parameter schema must be validated together.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross provider and parameter shape in poll: swallows operation errors and assert rejection.
