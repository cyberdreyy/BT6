# Q0278: populate then sign is not atomic in ConnectedStandardSolanaWallet.ts

## Question
handleSendTransaction populates, then signs, then broadcasts; can an attacker mutate the transaction object between those steps so the user approves one payload and another is signed via ConnectedStandardSolanaWallet.signMessage?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Pass an object with getters that change value between the populate and sign reads.
- Invariant to test: The signed payload must be a frozen snapshot of what was approved.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: pass a self-mutating object to ConnectedStandardSolanaWallet.signMessage and assert the signed payload equals the approved snapshot.
