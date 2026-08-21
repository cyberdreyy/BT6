# Q2288: moonpay sign input forwarded verbatim in FundingApi.ts

## Question
MoonpayOnRampApi.sign posts the caller's input body to the signing route; can an attacker include a walletAddress in FundingApi.moonpay that is not theirs so the signed on-ramp URL delivers funds elsewhere?

## Target
- File/function: [src/client/funding/FundingApi.ts](src/client/funding/FundingApi.ts) - FundingApi.moonpay, FundingApi.coinbase
- Entrypoint: privy.funding.*
- Attacker controls: which on-ramp is selected and the input object forwarded to it
- Exploit idea: Submit a foreign wallet address in the sign input.
- Invariant to test: The funded address must be validated against the authenticated user's wallets.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a foreign address to FundingApi.moonpay and assert rejection.
