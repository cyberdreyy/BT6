# Q0316: delegation consent payload built client-side in delegateWallet.ts

## Question
delegateWallet assembles rootWallet and delegatedWallets objects and hands them to the iframe consent step; can an attacker craft that payload through delegateWallet: checks address belongs to user so the consent screen describes one wallet while another is delegated?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Submit mismatched root and delegated entries.
- Invariant to test: The consent payload must be derived from validated account data and be exactly what is executed.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: submit a mismatched payload to delegateWallet: checks address belongs to user and assert refusal.
