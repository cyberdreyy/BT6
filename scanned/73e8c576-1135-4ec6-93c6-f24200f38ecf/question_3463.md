# Q3463: rpc errors collapse to null in EmbeddedSolanaWalletProvider.ts

## Question
SolanaClient.getBalance/getAccountInfo/getTokenAccountsByOwner return null on any error; can an attacker cause EmbeddedSolanaWalletProvider.request to report null so the app treats a funded account as empty (or the reverse) and routes a transfer incorrectly?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Return malformed RPC responses and observe the null results being consumed.
- Invariant to test: Failed reads must be distinguishable from zero-valued reads.
- Expected Immunefi impact: High - user funds routed to an address, chain, or asset the user did not choose (deposit, refund, on-ramp, or broadcast destination).
- Fast validation: Unit test: return an RPC error from EmbeddedSolanaWalletProvider.request and assert the caller receives an error, not null.
