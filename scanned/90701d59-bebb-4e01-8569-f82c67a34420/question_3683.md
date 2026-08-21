# Q3683: chain id switch emits an event apps trust in EmbeddedSolanaWalletProvider.ts

## Question
internalSwitchEthereumChain emits chainChanged after mutating internal state; can an attacker force a switch through EmbeddedSolanaWalletProvider.request so the app's UI shows one chain while signing occurs on another?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Trigger a switch during a pending signature and compare the UI chain to the signed chainId.
- Invariant to test: The chain displayed and the chain signed must be identical for every signature.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Integration test: interleave a switch with a signature through EmbeddedSolanaWalletProvider.request and assert consistency.
