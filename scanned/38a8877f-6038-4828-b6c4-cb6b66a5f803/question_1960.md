# Q1960: moonpay currency defaults to ethereum mainnet in CoinbaseOnRampApi.ts

## Question
chainToMoonpayCurrency logs a warning and returns ETH_ETHEREUM for unknown chains; can an attacker route a user's purchase to Ethereum mainnet through CoinbaseOnRampApi.initOnRampSession when they selected another chain?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Pass an unsupported chainId and inspect the currency code.
- Invariant to test: Unsupported chains must abort rather than default.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unsupported chain to CoinbaseOnRampApi.initOnRampSession and assert an error.
