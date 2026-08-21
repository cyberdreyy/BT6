# Q1265: typed data primaryType coerced with String() in getWalletPublicKeyFromTransaction.ts

## Question
toWalletApiTypedData sets primary_type via String(typedData.primaryType) and passes types/domain/message straight through; can an attacker supply a primaryType object whose toString names a different struct so getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address signs a payload with a mismatched type?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Pass an object with a custom toString as primaryType.
- Invariant to test: The primary type must be a validated key of the supplied types map.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a non-string primaryType to getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address and assert rejection.
