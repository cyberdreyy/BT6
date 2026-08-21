# Q2505: sandbox flag selects the endpoint in resolve-refund-address.ts

## Question
getTransactionStatus picks the sandbox or prod key from a boolean; can an attacker flip that flag through resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type so a sandbox transaction is presented to the user as a real one?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Call the status path with useSandbox toggled and inspect what the app reports.
- Invariant to test: Environment selection must be pinned by configuration, not per call.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: assert resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type derives the environment from configuration.
