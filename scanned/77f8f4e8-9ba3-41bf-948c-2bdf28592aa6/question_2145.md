# Q2145: options forwarded to the broadcaster in getWalletPublicKeyFromTransaction.ts

## Question
The options argument is passed to sendRawTransaction unchecked; can an attacker set options through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address that suppress preflight and hide a failing or malicious transaction?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Send skipPreflight and non-default commitment values.
- Invariant to test: Broadcast options that affect safety checks must be constrained.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address pins preflight-relevant options.
