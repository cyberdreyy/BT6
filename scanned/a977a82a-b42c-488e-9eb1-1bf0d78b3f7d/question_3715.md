# Q3715: onWalletCreated callback fires before confirmation in resolve-refund-address.ts

## Question
resolveRefundAddress invokes onWalletCreated after the create call returns; can an attacker use resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type so the app treats an unconfirmed wallet as ready and routes funds to it?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Return a create response and inspect the callback timing versus session refresh.
- Invariant to test: Callbacks signalling readiness must follow a confirmed session refresh.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type refreshes the user before invoking the callback.
