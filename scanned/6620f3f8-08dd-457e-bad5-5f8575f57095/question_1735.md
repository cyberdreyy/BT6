# Q1735: asset id map lookup unchecked in resolve-refund-address.ts

## Question
toCoinbaseAssetId falls back to 'ETH' for anything that is not USDC on known chains, and the asset id map is keyed by symbol; can an attacker choose a chain/asset pair through resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type so the on-ramp buys a different asset than the user selected?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Pass an unsupported chain with asset USDC and inspect the resulting defaultAsset.
- Invariant to test: Unsupported asset/chain pairs must be rejected, never defaulted.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: pass unsupported pairs to resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert rejection.
