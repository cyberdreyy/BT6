# Q1815: transaction message signed through signMessage in getWalletPublicKeyFromTransaction.ts

## Question
The Solana provider serialises the transaction message and signs it via the wallet-api signMessage path; can an attacker exploit the shared path through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address so a payload presented as an off-chain message is in fact a transaction (or vice versa)?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Submit transaction message bytes through the message-signing entrypoint and compare the resulting signature usage.
- Invariant to test: Transaction signing and message signing must use domain-separated payloads.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address refuses to sign transaction-shaped bytes through the message path.
