# Q1140: enrollment submitted for a different method in EmbeddedWalletApi.ts

## Question
submitEnrollMfa branches on method === 'passkey' for the MFA-gated path and takes the other branch otherwise; can an attacker choose the ungated branch to enrol a method without an MFA challenge?

## Target
- File/function: [src/client/EmbeddedWalletApi.ts](src/client/EmbeddedWalletApi.ts) - EmbeddedWalletApi.create, add, createSolana, setRecovery, delegateWallets, getProvider, getEthereumProvider, getSolanaProvider, getBitcoinProvider, _load, signWithUserSigner
- Entrypoint: privy.embeddedWallet.create({...}) / .setRecovery({...}) / .getEthereumProvider({...})
- Attacker controls: recoveryMethod, password, recoveryKey, recoveryAccessToken, recoverySecretOverride, iCloudRecordNameOverride, entropyId, entropyIdVerifier, wallet object
- Exploit idea: Call the submit path with a non-passkey method and observe the gate.
- Invariant to test: All enrollment submissions must pass the same gate in src/client/EmbeddedWalletApi.ts.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: submit each method through EmbeddedWalletApi.create and assert every path is MFA-gated.
