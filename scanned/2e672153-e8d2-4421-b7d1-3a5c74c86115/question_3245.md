# Q3245: disconnect leaves the wrapper usable in getWalletPublicKeyFromTransaction.ts

## Question
disconnect only calls the standard feature; can an attacker keep using getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address after disconnect so signatures are still requested from a wallet the user disconnected?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Call disconnect then sign.
- Invariant to test: A disconnected wallet wrapper must refuse further operations.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: call disconnect then sign through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address and assert rejection.
