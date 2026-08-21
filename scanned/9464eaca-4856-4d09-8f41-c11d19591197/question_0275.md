# Q0275: populate then sign is not atomic in getWalletPublicKeyFromTransaction.ts

## Question
handleSendTransaction populates, then signs, then broadcasts; can an attacker mutate the transaction object between those steps so the user approves one payload and another is signed via getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Pass an object with getters that change value between the populate and sign reads.
- Invariant to test: The signed payload must be a frozen snapshot of what was approved.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a self-mutating object to getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address and assert the signed payload equals the approved snapshot.
