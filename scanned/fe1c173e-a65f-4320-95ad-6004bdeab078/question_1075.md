# Q1075: timeout mapped to the same shape as success in resolve-refund-address.ts

## Question
The poll result mapper turns success-with-no-result into {status:'timeout'} and errors into timeouts too; can an attacker exploit that collapse through resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type so a failed deposit is presented as merely slow and the user re-sends funds?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Force error and timeout paths and compare what the caller sees.
- Invariant to test: Failure and timeout must be distinguishable to the caller.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: force each path in resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert distinct result shapes.
