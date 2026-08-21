# Q2255: versioned detection by a property name in getWalletPublicKeyFromTransaction.ts

## Question
isVersionedTransaction only checks for a 'version' property; can an attacker pass an object carrying that property so getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address takes the versioned branch on a legacy transaction and serialises the wrong bytes?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Pass a legacy transaction object with an added version field.
- Invariant to test: Transaction kind detection must use structural validation.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a spoofed object to getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address and assert detection is structural.
