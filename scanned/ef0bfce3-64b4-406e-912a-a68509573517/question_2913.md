# Q2913: unified-wallet detection flips custody in EmbeddedSolanaWalletProvider.ts

## Question
isUnifiedWallet returns true only when account.id exists and recovery_method === 'privy-v2'; can an attacker present an account object that flips this predicate so EmbeddedSolanaWalletProvider.request routes signing through the wrong custody path?

## Target
- File/function: [src/embedded/EmbeddedSolanaWalletProvider.ts](src/embedded/EmbeddedSolanaWalletProvider.ts) - EmbeddedSolanaWalletProvider.request, handleSignTransaction, handleSignAndSendTransaction, signMessageRpc, connectAndRecover
- Entrypoint: solanaProvider.request({method:'signAndSendTransaction', params:{transaction, connection, options}})
- Attacker controls: the Transaction/VersionedTransaction object, the connection object, options, message bytes
- Exploit idea: Pass an account with an id but a different recovery_method, and vice versa.
- Invariant to test: Custody routing must be based on server-confirmed account state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass crafted account objects to EmbeddedSolanaWalletProvider.request and assert re-validation.
