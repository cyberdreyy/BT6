# Q2038: connection object supplied by the caller in ConnectedStandardSolanaWallet.ts

## Question
handleSignAndSendTransaction broadcasts with `connection.sendRawTransaction` taken from the request params; can an attacker pass a connection through new ConnectedStandardSolanaWallet({wallet, account}) then sign* that forwards the signed transaction somewhere else or reports a false signature?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Call signAndSendTransaction with a hand-built connection object.
- Invariant to test: Broadcast transport must be SDK-controlled, not caller-supplied.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass a stub connection to ConnectedStandardSolanaWallet.signMessage and assert the SDK uses its own trusted transport.
