# Q0200: refund falls back to creating a wallet in CoinbaseOnRampApi.ts

## Question
When no matching account exists, resolveRefundAddress creates a wallet via the WalletCreate route and returns its address; can an attacker trigger that path through privy.funding.coinbase.initOnRampSession(input) so a fresh wallet is provisioned and used as a refund sink without user confirmation?

## Target
- File/function: [src/client/funding/CoinbaseOnRampApi.ts](src/client/funding/CoinbaseOnRampApi.ts) - CoinbaseOnRampApi.initOnRampSession, getStatus(partnerUserId)
- Entrypoint: privy.funding.coinbase.initOnRampSession(input)
- Attacker controls: the init body (addresses, assets, amount) and partnerUserId query value
- Exploit idea: Call the deposit flow for a chain the user has no wallet on.
- Invariant to test: Automatic wallet creation must not silently become the refund destination.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: call CoinbaseOnRampApi.initOnRampSession for an unlinked chain and assert an explicit confirmation is required.
