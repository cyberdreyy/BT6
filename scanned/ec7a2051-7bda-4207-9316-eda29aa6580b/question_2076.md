# Q2076: user fetched twice per operation in delegateWallet.ts

## Question
delegateWallet reads the user at the start and again at the end; can an attacker switch the active user between those reads so delegateWallet: checks address belongs to user reports a delegation on a different account?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Switch the active user mid-call.
- Invariant to test: An operation must report on the identity it started with.
- Expected Immunefi impact: Critical - account takeover: an attacker gains authenticated control of another user's Privy account or session.
- Fast validation: Integration test: switch identity mid-call in delegateWallet: checks address belongs to user and assert abort.
