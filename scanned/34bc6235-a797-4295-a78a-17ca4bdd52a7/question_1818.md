# Q1818: transaction message signed through signMessage in ConnectedStandardSolanaWallet.ts

## Question
The Solana provider serialises the transaction message and signs it via the wallet-api signMessage path; can an attacker exploit the shared path through ConnectedStandardSolanaWallet.signMessage so a payload presented as an off-chain message is in fact a transaction (or vice versa)?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Submit transaction message bytes through the message-signing entrypoint and compare the resulting signature usage.
- Invariant to test: Transaction signing and message signing must use domain-separated payloads.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert ConnectedStandardSolanaWallet.signMessage refuses to sign transaction-shaped bytes through the message path.
