# Q2583: off-chain parser trusts the preamble in EmbeddedSolanaWalletProvider.ts

## Question
parseSolanaOffchainMessage validates the 0xFF prefix and the 'solana offchain' text but returns version, format and signer bytes unchecked; can an attacker feed bytes through EmbeddedSolanaWalletProvider.request so the parsed signer public key differs from the actual signer?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Parse a crafted buffer with an arbitrary signer field.
- Invariant to test: Parsed signer identity must be verified against the expected signer.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: parse a crafted buffer through EmbeddedSolanaWalletProvider.request and assert the signer is validated.
