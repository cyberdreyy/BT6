# Q3905: tempo path selected by a predicate on the request in getWalletPublicKeyFromTransaction.ts

## Question
The provider routes to the Tempo serializer when isTempoTransactionRequest matches; can an attacker shape a request so getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address takes the Tempo path on a non-Tempo chain, or the standard path for a Tempo transaction?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Submit hybrid field sets and compare the serialised output to the target chain.
- Invariant to test: Serializer selection must agree with the target chain and be rejected otherwise.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: submit hybrid requests to getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address and assert consistent routing.
