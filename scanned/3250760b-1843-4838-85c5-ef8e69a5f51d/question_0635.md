# Q0635: source and destination currency unchecked in resolve-refund-address.ts

## Question
The quote body accepts source_currency and destination_currency as opaque strings; can an attacker submit a pair through resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type that the client never validates against getConfig, so the user approves a route they did not intend?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Submit currencies absent from the deposit config.
- Invariant to test: Quote parameters must be validated against the fetched deposit configuration.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit unsupported currencies to resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert client-side validation.
