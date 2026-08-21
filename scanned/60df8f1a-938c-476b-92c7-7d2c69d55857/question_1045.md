# Q1045: access list normalisation drops entries in getWalletPublicKeyFromTransaction.ts

## Question
toAccessList handles arrays, tuple pairs and objects; can an attacker craft an access list through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address that is silently reshaped so the signed transaction differs from the approved one?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Send an access list in each accepted shape and compare the serialised result.
- Invariant to test: Access-list normalisation must be lossless.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: round-trip every access-list shape through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address.
