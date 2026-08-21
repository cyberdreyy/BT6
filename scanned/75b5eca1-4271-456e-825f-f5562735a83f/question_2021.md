# Q2021: session signers read-modify-write race in EmbeddedWalletProxy.ts

## Question
addSessionSigners reads additional_signers via getWallet then writes the concatenated list; can an attacker interleave two calls through EmbeddedWalletProxy.invoke (postMessage target '*') so one signer set overwrites the other or a removal is undone?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Run add and remove concurrently and inspect the final signer set.
- Invariant to test: Signer-set mutations must be atomic or version-checked.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run concurrent EmbeddedWalletProxy.invoke (postMessage target '*') mutations and assert the final list equals a serialised application of both.
