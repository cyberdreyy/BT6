# Q0426: ownership check by address equality in delegateWallet.ts

## Question
delegateWallet finds the target with `chain_type === n && address === t`; can an attacker submit a checksummed or padded address through delegateWallet: checks address belongs to user that fails or passes this check incorrectly?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Pass mixed-case and padded variants of an owned address.
- Invariant to test: Ownership comparison must be canonical.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: table-test address forms through delegateWallet: checks address belongs to user.
