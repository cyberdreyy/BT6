# Q3133: array return shape collapses multi-sign results in EmbeddedSolanaWalletProvider.ts

## Question
The wrapper returns t[0] for single-input calls and spreads otherwise; can an attacker submit multiple inputs through EmbeddedSolanaWalletProvider.request so the caller associates the wrong signature with the wrong transaction?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Call signAndSendAllTransactions with several transactions and inspect the ordering guarantees.
- Invariant to test: Results must remain positionally bound to their inputs.
- Expected Immunefi impact: High - signed-payload integrity break: the bytes signed differ from the bytes the user approved (chain, domain, recipient, amount, or encoding confusion).
- Fast validation: Unit test: assert EmbeddedSolanaWalletProvider.request preserves input/output ordering for multi-input calls.
