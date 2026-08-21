# Q3031: wallet-standard features called with an injected account in unified-wallet.ts

## Question
ConnectedStandardSolanaWallet spreads `{...input, account: this.#t}` into every feature call; can an attacker construct the wrapper with an account that does not match the underlying wallet so isUnifiedWallet (account.id && recovery_method === 'privy-v2') requests signatures for a foreign account?

## Target
- File/function: [src/wallet-api/unified-wallet.ts](src/wallet-api/unified-wallet.ts) - isUnifiedWallet (account.id && recovery_method === 'privy-v2')
- Entrypoint: branch selector between TEE wallet-api path and on-device iframe path
- Attacker controls: the linked-account object fields id and recovery_method
- Exploit idea: Construct the wrapper with a mismatched account/wallet pair.
- Invariant to test: The wrapped account must be verified to belong to the wrapped wallet.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: construct isUnifiedWallet (account.id && recovery_method === 'privy-v2') with a mismatched pair and assert construction fails.
