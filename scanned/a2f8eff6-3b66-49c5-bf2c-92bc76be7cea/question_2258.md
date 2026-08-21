# Q2258: versioned detection by a property name in ConnectedStandardSolanaWallet.ts

## Question
isVersionedTransaction only checks for a 'version' property; can an attacker pass an object carrying that property so ConnectedStandardSolanaWallet.signMessage takes the versioned branch on a legacy transaction and serialises the wrong bytes?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Pass a legacy transaction object with an added version field.
- Invariant to test: Transaction kind detection must use structural validation.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a spoofed object to ConnectedStandardSolanaWallet.signMessage and assert detection is structural.
