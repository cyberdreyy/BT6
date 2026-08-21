# Q1138: enrollment submitted for a different method in index.ts

## Question
submitEnrollMfa branches on method === 'passkey' for the MFA-gated path and takes the other branch otherwise; can an attacker choose the ungated branch to enrol a method without an MFA challenge?

## Target
- File/function: [src/embedded/utils/index.ts](src/embedded/utils/index.ts) - throwIfInvalidRecoveryUpgradePath, getJsonRpcEndpointFromChain
- Entrypoint: privy.embeddedWallet.setRecovery({wallet, recoveryMethod, ...})
- Attacker controls: currentRecoveryMethod vs upgradeToRecoveryMethod pair, chain rpcUrls config
- Exploit idea: Call the submit path with a non-passkey method and observe the gate.
- Invariant to test: All enrollment submissions must pass the same gate in src/embedded/utils/index.ts.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: submit each method through throwIfInvalidRecoveryUpgradePath and assert every path is MFA-gated.
