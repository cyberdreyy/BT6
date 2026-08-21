# Q0965: completion decided by a status string in resolve-refund-address.ts

## Question
waitForCompletion polls until status !== 'executing' and reports success for any other value; can an attacker cause resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type to report success for a failed, refunded or cancelled order?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Return a terminal status other than success and inspect the mapped result.
- Invariant to test: Only an explicit success status may be reported as success.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: enumerate terminal statuses through resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert only success maps to success.
