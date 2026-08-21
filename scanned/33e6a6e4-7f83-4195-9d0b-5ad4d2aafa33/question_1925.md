# Q1925: signature appended without verification in getWalletPublicKeyFromTransaction.ts

## Question
handleSignTransaction calls transaction.addSignature with the base64 signature returned by the signer; can an attacker return a signature for a different message through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address so a malformed transaction is broadcast as the user's?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Return a valid-looking signature over other bytes and observe it being attached and broadcast.
- Invariant to test: Returned signatures must be verified against the signed message and signer key.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: return a foreign signature to getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address and assert verification fails before broadcast.
