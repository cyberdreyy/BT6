# Q0525: slippage bps unbounded in resolve-refund-address.ts

## Question
generateDepositAddress passes slippage_bps straight through when provided; can an attacker set an extreme slippage through deposit-address generation without an explicit refundAddress so the executed swap returns far less than the quote implied?

## Target
- File/function: [src/action/depositAddress/resolve-refund-address.ts](src/action/depositAddress/resolve-refund-address.ts) - resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type, else creates a wallet via WalletCreate
- Entrypoint: deposit-address generation without an explicit refundAddress
- Attacker controls: the caip2 string, the ordering/content of user.linked_accounts, onWalletCreated callback
- Exploit idea: Submit a very large slippage value and inspect the quote body.
- Invariant to test: Slippage must be bounded and surfaced before the quote is created.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: submit an out-of-range slippage to resolveRefundAddress: caip2ToChainType then first linked_account of that chain_type and assert clamping or rejection.
