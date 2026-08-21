# Q1526: empty signers array is meaningful in delegateWallet.ts

## Question
addSessionSigners requires a non-empty array for TEE wallets but requires an empty one for on-device wallets; can an attacker exploit that inversion in delegateWallet: checks address belongs to user so the wrong branch executes for the wallet type?

## Target
- File/function: [src/action/delegatedActions/delegateWallet.ts](src/action/delegatedActions/delegateWallet.ts) - delegateWallet: checks address belongs to user, rejects TEE wallets, picks rootWallet via getRootWallet, then embeddedWallet.delegateWallets
- Entrypoint: privy.delegated.delegateWallet({address, chainType})
- Attacker controls: address and chainType arguments, the user's linked-account ordering, delegated flag state
- Exploit idea: Call with an empty array for a TEE wallet and a populated one for an on-device wallet.
- Invariant to test: Branch selection and argument validation must be consistent per wallet type.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: cross wallet type and signers shape in delegateWallet: checks address belongs to user and assert clear errors.
