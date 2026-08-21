# Q1295: attempt arithmetic derived from the interval in resolve-refund-address.ts

## Question
The attempt count is ceil(timeout/interval) with a caller-supplied interval; can an attacker pass a tiny interval through resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type to multiply requests, or a huge one so the deposit is never observed?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Pass extreme pollIntervalMs values.
- Invariant to test: Polling parameters must be bounded by the SDK.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass extreme intervals to resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert clamping.
