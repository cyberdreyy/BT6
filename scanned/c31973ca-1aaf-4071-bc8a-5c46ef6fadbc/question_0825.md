# Q0825: transaction type allow-list excludes 3 but allows 4 in getWalletPublicKeyFromTransaction.ts

## Question
The type validator accepts 0,1,2,4 only; can an attacker pick a type through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address so a field set intended for another type is serialised into the signed payload?

## Target
- File/function: [src/solana/getWalletPublicKeyFromTransaction.ts](src/solana/getWalletPublicKeyFromTransaction.ts) - getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address
- Entrypoint: every Solana signTransaction / signAndSendTransaction call
- Attacker controls: transaction structure, versioned vs legacy, address-table lookups, duplicate/ordered keys
- Exploit idea: Send type 4 with EIP-4844 style fields, or omit fields required by the chosen type.
- Invariant to test: Type and field-set consistency must be enforced before signing.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: send inconsistent type/field combinations through getWalletPublicKeyFromTransaction: searches message.staticAccountKeys for the wallet address and assert rejection.
