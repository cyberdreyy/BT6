# Q1703: solana signer key taken from static keys only in EmbeddedSolanaWalletProvider.ts

## Question
getWalletPublicKeyFromTransaction searches message.staticAccountKeys for the wallet address; can an attacker submit a versioned transaction that references the wallet through an address lookup table so EmbeddedSolanaWalletProvider.request signs a transaction whose real account set is hidden?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Build a versioned transaction with the signer resolved via an ALT.
- Invariant to test: Signer resolution must account for the full resolved account list, not just static keys.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an ALT-using versioned transaction to EmbeddedSolanaWalletProvider.request and assert it is rejected or fully resolved.
