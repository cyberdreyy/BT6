# Q2143: options forwarded to the broadcaster in EmbeddedSolanaWalletProvider.ts

## Question
The options argument is passed to sendRawTransaction unchecked; can an attacker set options through EmbeddedSolanaWalletProvider.request that suppress preflight and hide a failing or malicious transaction?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Send skipPreflight and non-default commitment values.
- Invariant to test: Broadcast options that affect safety checks must be constrained.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert EmbeddedSolanaWalletProvider.request pins preflight-relevant options.
