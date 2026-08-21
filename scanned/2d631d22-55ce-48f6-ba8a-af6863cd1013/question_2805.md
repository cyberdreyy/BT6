# Q2805: psbt forwarded without inspection in getWalletPublicKeyFromTransaction.ts

## Question
signTransaction forwards the psbt argument verbatim to the iframe; can an attacker submit a psbt through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address whose outputs differ from what the app displayed?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Submit a psbt with an added output and observe no client-side checks.
- Invariant to test: The SDK must surface or verify the outputs it asks the user to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address extracts and exposes psbt outputs for confirmation.
