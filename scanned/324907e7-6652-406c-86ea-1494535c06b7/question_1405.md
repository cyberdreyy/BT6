# Q1405: abort signal supplied by the caller in resolve-refund-address.ts

## Question
poll checks a caller-supplied AbortSignal; can an attacker abort resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type at a chosen moment so the app treats a completed deposit as aborted and issues a duplicate?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Abort right after the funds land.
- Invariant to test: Abort must not change the recorded outcome of a settled deposit.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Integration test: abort resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type after settlement and assert the state reflects settlement.
