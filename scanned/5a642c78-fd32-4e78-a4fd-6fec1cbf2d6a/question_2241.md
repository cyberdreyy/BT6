# Q2241: remove clears every signer in EmbeddedWalletProxy.ts

## Question
removeSessionSigners writes additional_signers: [] or revokes all delegations; can an attacker use EmbeddedWalletProxy.invoke (postMessage target '*') to clear another party's legitimate signer while keeping their own access?

## Target
- File/function: [src/embedded/EmbeddedWalletProxy.ts](src/embedded/EmbeddedWalletProxy.ts) - EmbeddedWalletProxy.invoke (postMessage target '*'), handleEmbeddedWalletMessages, invokeWithMfa, waitForReady, reload, ping, rpcWallet, signWithUserSigner, setRecovery, delegateWallets
- Entrypoint: privy.embeddedWallet.onMessage(msg) fed from the host page's message listener
- Attacker controls: the {id, event, data, error} object handed to onMessage, its arrival order and timing
- Exploit idea: Call the remove path while multiple signers exist.
- Invariant to test: Signer removal must be scoped to the signer the user selected.
- Expected Immunefi impact: High - authorization bypass: a privileged wallet or MFA operation completes without the user-approval gate the app relies on.
- Fast validation: Unit test: call EmbeddedWalletProxy.invoke (postMessage target '*') with multiple signers present and assert only the requested one is removed.
