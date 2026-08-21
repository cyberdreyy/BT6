# Q3026: wallet-standard features called with an injected account in isVersionedTransaction.ts

## Question
ConnectedStandardSolanaWallet spreads `{...input, account: this.#t}` into every feature call; can an attacker construct the wrapper with an account that does not match the underlying wallet so isVersionedTransaction ('version' in tx) requests signatures for a foreign account?

## Target
- File/function: [src/solana/isVersionedTransaction.ts](src/solana/isVersionedTransaction.ts) - isVersionedTransaction ('version' in tx)
- Entrypoint: Solana provider transaction handling
- Attacker controls: any object shaped to satisfy or defeat the 'version' in tx check
- Exploit idea: Construct the wrapper with a mismatched account/wallet pair.
- Invariant to test: The wrapped account must be verified to belong to the wrapped wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: construct isVersionedTransaction ('version' in tx) with a mismatched pair and assert construction fails.
