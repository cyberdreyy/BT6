# Q3138: array return shape collapses multi-sign results in ConnectedStandardSolanaWallet.ts

## Question
The wrapper returns t[0] for single-input calls and spreads otherwise; can an attacker submit multiple inputs through ConnectedStandardSolanaWallet.signMessage so the caller associates the wrong signature with the wrong transaction?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Call signAndSendAllTransactions with several transactions and inspect the ordering guarantees.
- Invariant to test: Results must remain positionally bound to their inputs.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: assert ConnectedStandardSolanaWallet.signMessage preserves input/output ordering for multi-input calls.
