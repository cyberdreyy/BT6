# Q0828: transaction type allow-list excludes 3 but allows 4 in ConnectedStandardSolanaWallet.ts

## Question
The type validator accepts 0,1,2,4 only; can an attacker pick a type through ConnectedStandardSolanaWallet.signMessage so a field set intended for another type is serialised into the signed payload?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Send type 4 with EIP-4844 style fields, or omit fields required by the chosen type.
- Invariant to test: Type and field-set consistency must be enforced before signing.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: send inconsistent type/field combinations through ConnectedStandardSolanaWallet.signMessage and assert rejection.
