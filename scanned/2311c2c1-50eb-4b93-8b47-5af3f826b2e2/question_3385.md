# Q3385: funding api selects the provider by property in resolve-refund-address.ts

## Question
FundingApi exposes moonpay and coinbase; can an attacker cause resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type to route a funding request to a provider the app did not configure, with parameters shaped for the other?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Call each provider with the other's parameter shape.
- Invariant to test: Provider selection and parameter schema must be validated together.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: cross provider and parameter shape in resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert rejection.
