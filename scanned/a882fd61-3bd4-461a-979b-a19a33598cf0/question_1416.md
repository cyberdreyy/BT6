# Q1416: signers array unvalidated in delegateWallet.ts

## Question
addSessionSigners concatenates the caller's signers onto the existing list; can an attacker add a signer key they control through delegateWallet: checks address belongs to user so future server-side signing is possible without the user?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Pass an attacker signer entry and inspect the resulting wallet record.
- Invariant to test: Every added signer must be user-approved and validated.
- Expected Immunefi impact: Critical - direct theft of user funds: a signature or transaction the victim never approved is produced, broadcast, or made producible from their wallet.
- Fast validation: Unit test: pass an arbitrary signer to delegateWallet: checks address belongs to user and assert an approval gate.
