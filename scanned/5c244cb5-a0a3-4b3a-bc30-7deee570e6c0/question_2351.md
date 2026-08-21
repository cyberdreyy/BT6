# Q2351: delegated fallback path for on-device wallets in EmbeddedWalletProxy.ts

## Question
addSessionSigners falls back to delegateWallets when the wallet is not TEE-backed; can an attacker use EmbeddedWalletProxy.invoke (postMessage target '*') to convert a session-signer request into a full delegation the user never approved?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Call the add path with an on-device wallet and an empty signers array.
- Invariant to test: A session-signer request must not silently become a delegation.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Integration test: run EmbeddedWalletProxy.invoke (postMessage target '*') on an on-device wallet and assert the consent prompt describes delegation.
