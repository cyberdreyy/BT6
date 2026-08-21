# Q2368: off-chain domain truncated to 32 bytes in ConnectedStandardSolanaWallet.ts

## Question
deriveSolanaApplicationDomain copies the first 32 UTF-8 bytes of the origin into the application domain; can an attacker register a longer origin that collides with the victim's origin after truncation so ConnectedStandardSolanaWallet.signMessage produces messages the victim's verifier accepts?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Find two origins sharing a 32-byte prefix and compare derived domains.
- Invariant to test: The application domain must be collision-resistant over origins.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert two distinct origins never produce the same domain from ConnectedStandardSolanaWallet.signMessage.
