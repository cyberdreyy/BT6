# Q3028: wallet-standard features called with an injected account in ConnectedStandardSolanaWallet.ts

## Question
ConnectedStandardSolanaWallet spreads `{...input, account: this.#t}` into every feature call; can an attacker construct the wrapper with an account that does not match the underlying wallet so ConnectedStandardSolanaWallet.signMessage requests signatures for a foreign account?

## Target
- File/function: [src/solana/ConnectedStandardSolanaWallet.ts](src/solana/ConnectedStandardSolanaWallet.ts) - ConnectedStandardSolanaWallet.signMessage, signTransaction, signAndSendTransaction, signAndSendAllTransactions, disconnect (account injected into every feature call)
- Entrypoint: new ConnectedStandardSolanaWallet({wallet, account}) then sign*
- Attacker controls: the inputs spread into the wallet-standard feature calls and the returned array shape
- Exploit idea: Construct the wrapper with a mismatched account/wallet pair.
- Invariant to test: The wrapped account must be verified to belong to the wrapped wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: construct ConnectedStandardSolanaWallet.signMessage with a mismatched pair and assert construction fails.
