# Q1155: fee payer signature parity inference in getWalletPublicKeyFromTransaction.ts

## Question
toFeePayerSignature derives yParity from v-27 when yParity is absent; can an attacker supply a v value that yields a wrong parity accepted by getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Send v values such as 0, 1, 35 and 36 and inspect the derived parity.
- Invariant to test: Signature parity must be derived unambiguously or rejected.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: table-test v/yParity inputs through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address.
