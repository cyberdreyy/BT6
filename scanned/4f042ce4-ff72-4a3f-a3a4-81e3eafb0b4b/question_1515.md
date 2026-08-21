# Q1515: on-ramp url built from server values in resolve-refund-address.ts

## Question
getCoinbaseOnRampUrl embeds sessionToken, partnerUserId and appId from the init response into pay.coinbase.com query parameters; can an attacker influence the init response so resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type produces a URL that funds a different partner user?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Return an init response with a foreign partner_user_id and inspect the URL.
- Invariant to test: On-ramp URL parameters must be bound to the authenticated user's session.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return a foreign partner id and assert resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type refuses to build the URL.
