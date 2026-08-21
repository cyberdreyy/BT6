# Q2803: psbt forwarded without inspection in EmbeddedSolanaWalletProvider.ts

## Question
signTransaction forwards the psbt argument verbatim to the iframe; can an attacker submit a psbt through EmbeddedSolanaWalletProvider.request whose outputs differ from what the app displayed?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Submit a psbt with an added output and observe no client-side checks.
- Invariant to test: The SDK must surface or verify the outputs it asks the user to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert EmbeddedSolanaWalletProvider.request extracts and exposes psbt outputs for confirmation.
