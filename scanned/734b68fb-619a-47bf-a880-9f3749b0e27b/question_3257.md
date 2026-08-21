# Q3257: no per-wallet rate or nonce state in get-wallet.ts

## Question
Nothing in src/wallet-api/get-wallet.ts tracks a per-wallet request counter; can an attacker replay or reorder signed operations through getWallet(): WalletGet by wallet_id so a signing sequence executes in an order the user never intended?

## Target
- File/function: [src/wallet-api/get-wallet.ts](src/wallet-api/get-wallet.ts) - getWallet(): WalletGet by wallet_id, returns additional_signers
- Entrypoint: addSessionSigners read-modify-write
- Attacker controls: wallet_id value and the returned additional_signers list used for the next write
- Exploit idea: Capture two operations and deliver them out of order.
- Invariant to test: Wallet operations must carry a monotonic per-wallet nonce.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: reorder two captured getWallet(): WalletGet by wallet_id operations and assert the second is rejected.
