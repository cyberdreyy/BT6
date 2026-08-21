# Q1048: access list normalisation drops entries in ConnectedStandardSolanaWallet.ts

## Question
toAccessList handles arrays, tuple pairs and objects; can an attacker craft an access list through ConnectedStandardSolanaWallet.signMessage that is silently reshaped so the signed transaction differs from the approved one?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Send an access list in each accepted shape and compare the serialised result.
- Invariant to test: Access-list normalisation must be lossless.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: round-trip every access-list shape through ConnectedStandardSolanaWallet.signMessage.
