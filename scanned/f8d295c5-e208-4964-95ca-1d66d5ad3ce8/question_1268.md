# Q1268: typed data primaryType coerced with String() in ConnectedStandardSolanaWallet.ts

## Question
toWalletApiTypedData sets primary_type via String(typedData.primaryType) and passes types/domain/message straight through; can an attacker supply a primaryType object whose toString names a different struct so ConnectedStandardSolanaWallet.signMessage signs a payload with a mismatched type?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Pass an object with a custom toString as primaryType.
- Invariant to test: The primary type must be a validated key of the supplied types map.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a non-string primaryType to ConnectedStandardSolanaWallet.signMessage and assert rejection.
