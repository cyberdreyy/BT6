# Q2945: usdc map missing for a supported chain in resolve-refund-address.ts

## Question
UsdcAddressMap covers a fixed chain set; can an attacker select a chain through resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type where the lookup is undefined so every token compares false and the flow proceeds with the wrong asset assumption?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Pass a chain absent from the map.
- Invariant to test: Unknown chains must abort the asset decision.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass an unmapped chain to resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert an explicit error.
