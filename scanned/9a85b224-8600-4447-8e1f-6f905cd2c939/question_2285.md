# Q2285: moonpay sign input forwarded verbatim in resolve-refund-address.ts

## Question
MoonpayOnRampApi.sign posts the caller's input body to the signing route; can an attacker include a walletAddress in resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type that is not theirs so the signed on-ramp URL delivers funds elsewhere?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Submit a foreign wallet address in the sign input.
- Invariant to test: The funded address must be validated against the authenticated user's wallets.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit a foreign address to resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert rejection.
