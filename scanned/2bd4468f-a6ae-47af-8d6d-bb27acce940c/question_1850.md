# Q1850: network defaulted on unknown chain in CoinbaseOnRampApi.ts

## Question
toCoinbaseBlockchainFromChainId returns undefined for unknown chains while the URL builder still sets defaultNetwork; can an attacker use CoinbaseOnRampApi.initOnRampSession so the on-ramp delivers funds on an unintended network?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Pass an unsupported chainId through the funding path.
- Invariant to test: An unknown chain must abort the funding flow.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unsupported chainId to CoinbaseOnRampApi.initOnRampSession and assert abort.
