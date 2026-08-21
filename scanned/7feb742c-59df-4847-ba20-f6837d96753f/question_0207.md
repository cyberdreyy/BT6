# Q0207: imported flag flips the root in revokeWallets.ts

## Question
getRootWallet returns the account itself when imported is true; can an attacker present an account object with imported set through privy.delegated.revokeWallets() so revokeWallets: requires at least one delegated wallet treats an arbitrary wallet as its own root?

## Target
- File/function: [src/action/delegatedActions/revokeWallets.ts](src/action/delegatedActions/revokeWallets.ts) - revokeWallets: requires at least one delegated wallet, then delegated.revoke() (revokes all)
- Entrypoint: privy.delegated.revokeWallets()
- Attacker controls: timing relative to delegate calls and to session refresh
- Exploit idea: Pass a crafted account with imported true.
- Invariant to test: Account flags used for delegation must come from server-confirmed state.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass {imported:true} on a crafted account to revokeWallets: requires at least one delegated wallet and assert re-validation.
