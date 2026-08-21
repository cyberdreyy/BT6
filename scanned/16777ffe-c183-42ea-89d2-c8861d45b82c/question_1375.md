# Q1375: typed data accepted as a JSON string in getWalletPublicKeyFromTransaction.ts

## Question
toWalletApiTypedData JSON.parses string input before use; can an attacker pass a string whose parse result differs from what the app displayed, so getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address signs different typed data?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Pass a JSON string with duplicate keys or unusual escaping and compare the parsed structure.
- Invariant to test: String and object inputs must produce identical, validated structures.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass duplicate-key JSON to getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address and assert deterministic, validated parsing.
