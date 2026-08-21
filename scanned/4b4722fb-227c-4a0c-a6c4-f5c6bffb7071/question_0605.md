# Q0605: bigint stringification changes values in getWalletPublicKeyFromTransaction.ts

## Question
handleSignTransaction converts bigint fields with toHex over Object.keys, including nested call values; can an attacker craft a field whose conversion is lossy so getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address signs a different value than displayed?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Submit values at the edges of the bigint/number/hex conversions and diff the serialised output.
- Invariant to test: Numeric conversion must be exact and total for every signed field.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: property-test numeric fields through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address and assert round-trip equality.
