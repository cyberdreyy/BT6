# Q0976: TEE wallets rejected only client-side in delegateWallet.ts

## Question
delegateWallet and revokeWallets throw unsupported_wallet_type for unified (privy-v2) wallets based on the account object; can an attacker present an account through delegateWallet: checks address belongs to user that evades the check and reaches the delegation path?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Pass an account missing the id field or with a different recovery_method.
- Invariant to test: Custody-type checks must use server-confirmed account records.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass evasive account objects to delegateWallet: checks address belongs to user and assert re-validation.
