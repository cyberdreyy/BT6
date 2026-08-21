# Q2808: psbt forwarded without inspection in ConnectedStandardSolanaWallet.ts

## Question
signTransaction forwards the psbt argument verbatim to the iframe; can an attacker submit a psbt through ConnectedStandardSolanaWallet.signMessage whose outputs differ from what the app displayed?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Submit a psbt with an added output and observe no client-side checks.
- Invariant to test: The SDK must surface or verify the outputs it asks the user to sign.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: assert ConnectedStandardSolanaWallet.signMessage extracts and exposes psbt outputs for confirmation.
