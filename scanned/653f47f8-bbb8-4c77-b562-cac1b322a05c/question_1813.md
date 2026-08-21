# Q1813: transaction message signed through signMessage in EmbeddedSolanaWalletProvider.ts

## Question
The Solana provider serialises the transaction message and signs it via the wallet-api signMessage path; can an attacker exploit the shared path through EmbeddedSolanaWalletProvider.request so a payload presented as an off-chain message is in fact a transaction (or vice versa)?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Submit transaction message bytes through the message-signing entrypoint and compare the resulting signature usage.
- Invariant to test: Transaction signing and message signing must use domain-separated payloads.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert EmbeddedSolanaWalletProvider.request refuses to sign transaction-shaped bytes through the message path.
