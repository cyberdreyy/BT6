### Title
EIP-8130 canonical contract addresses are hardcoded and assumed deployed on every chain with no deployment check or force-deploy, unlike other Base system contracts - (File: crates/common/consensus/src/transaction/eip8130/addresses.rs)

### Summary
`Eip8130Contracts` hardcodes the CREATE2-derived addresses of `ACCOUNT_CONFIG`, `DEFAULT_ACCOUNT`, `P256_AUTHENTICATOR`, `WEBAUTHN_AUTHENTICATOR`, and `DELEGATE_AUTHENTICATOR`, asserting they are "deployed at the same address on every EVM chain" via Nick's factory [1](#0-0) . Unlike the Canyon create2-deployer, `BaseTime`, and the EIP-8130 `NonceManager`, which all have explicit transition hooks that force-deploy or existence-check them at fork activation, there is no equivalent hook for `AccountConfiguration` or the authenticator contracts — the code explicitly documents this as an assumption rather than an enforced invariant.

### Finding Description
The Zenith transition hook that runs at every block boundary only plants a code stub on the `NonceManager` account, and its own comment states the reasoning: `AccountConfiguration` "is a genuinely deployed contract (it carries code) on every chain where EIP-8130 is enabled" [2](#0-1) . This is stated as a fact but is never verified by the protocol — there is no consensus-level check that `ACCOUNT_CONFIG`, `DEFAULT_ACCOUNT`, `P256_AUTHENTICATOR`, `WEBAUTHN_AUTHENTICATOR`, or `DELEGATE_AUTHENTICATOR` actually carry the expected bytecode at their hardcoded, CREATE2-derived addresses before Zenith activates.

Despite this, the enshrined native validation path dispatches purely by address comparison, with no existence check:
- `AuthenticatorDispatch::authenticate` routes to native P-256/WebAuthn/Delegate logic solely based on address equality with the hardcoded constants [3](#0-2) .
- `AccountConfigurationStorage` reads/writes persistent actor and account state directly at `Eip8130Contracts::ACCOUNT_CONFIG` via raw storage slots, bypassing any EVM call or code-existence check [4](#0-3) .
- The mempool admission path (`authenticator_allowed_for_tx_path`) and block-validation allowlist (`is_canonical_authenticator`) both accept these hardcoded addresses unconditionally [5](#0-4) [6](#0-5) .

If `Zenith` activates on any chain (a devnet, testnet, or a newly spun-up Base-based chain) before the off-chain CREATE2 deployment transactions for these contracts are actually broadcast at that hardcoded address (e.g., deployer key unavailable, factory not yet seeded, chain-specific deployment omitted), the enshrined path will still fully authenticate and apply AA transactions and persist `AccountConfiguration` storage — believing a contract exists at that address — while ordinary EVM `CALL`/`STATICCALL` to the same address (e.g., wallet code invoking `AccountConfiguration.verifySignature()`/`applySignedActorChanges()` on the non-native path, per the crate's own parity documentation [7](#0-6) ) would hit empty bytecode and revert. This is the same class of bug as the reported Permit2 issue: a hardcoded external/off-chain-deployed contract address assumed present on every chain, with no deployment guarantee or on-chain verification, whose absence causes contract functionality to silently diverge or fail once actually exercised.

### Impact Explanation
This is Medium severity: it does not directly grant an attacker funds, but a chain where these contracts are not actually deployed (or are deployed with different bytecode than the pinned `*_INIT_CODE_HASH`) would experience a consensus-relevant divergence between the enshrined native authentication/authorization logic and the real (empty or different) on-chain contract state at those addresses — leading to either wrongly authenticated/authorized EIP-8130 transactions, or wholesale inability to use account-abstraction features, without any explicit failure signal at genesis or activation time.

### Likelihood Explanation
Likelihood is moderate: it requires a chain operator to activate `Zenith` without first (or correctly) deploying the pinned EIP-8130 contracts via Nick's factory at the exact salts, which is an operational/deployment-process risk rather than something a remote attacker can trigger directly, but it is a systemic gap since none of the affected addresses (`ACCOUNT_CONFIG`, `DEFAULT_ACCOUNT`, authenticators) have the same force-deploy or existence-verification treatment that Canyon, `BaseTime`, and the `NonceManager` receive.

### Recommendation
Add a Zenith-activation guard (mirroring `ensure_eip8130_system_accounts`/`Canyon::ensure_create2_deployer`) that verifies `ACCOUNT_CONFIG`, `DEFAULT_ACCOUNT`, `P256_AUTHENTICATOR`, `WEBAUTHN_AUTHENTICATOR`, and `DELEGATE_AUTHENTICATOR` carry their expected `*_INIT_CODE_HASH` bytecode before enabling the enshrined dispatch path, and fail loudly (reject activation / halt) rather than silently trusting the hardcoded addresses are populated.

### Proof of Concept
Not applicable — this is a deployment/configuration-verification gap rather than an exploit against a running, correctly-configured Base mainnet/testnet; it manifests only on a chain where Zenith is activated without the pinned EIP-8130 contracts deployed at their hardcoded CREATE2 addresses, as documented (and assumed true without enforcement) in [2](#0-1) .

### Citations

**File:** crates/common/consensus/src/transaction/eip8130/addresses.rs (L35-41)
```rust
impl Eip8130Contracts {
    /// Nick's deterministic-deployment proxy (the "Arachnid" CREATE2 factory),
    /// deployed at the same address on every EVM chain. Every EIP-8130 contract is
    /// deployed by sending `salt || init_code` to this factory.
    ///
    /// <https://github.com/Arachnid/deterministic-deployment-proxy>
    pub const CREATE2_FACTORY: Address = address!("0x4e59b44847b379578588920cA78FbF26c0B4956C");
```

**File:** crates/common/consensus/src/transaction/eip8130/addresses.rs (L156-164)
```rust
    /// Returns `true` if `authenticator` is in the canonical deployed-contract
    /// allowlist ([`Self::CANONICAL_AUTHENTICATORS`]).
    ///
    /// This intentionally does not account for the native ecrecover sentinel
    /// (`address(1)`), which is handled separately by the protocol.
    #[must_use]
    pub fn is_canonical_authenticator(authenticator: &Address) -> bool {
        Self::CANONICAL_AUTHENTICATORS.contains(authenticator)
    }
```

**File:** crates/common/evm2/src/zenith.rs (L22-31)
```rust

/// Code-less EIP-8130 system accounts that hold persistent storage but carry no code, and
/// therefore must be made non-empty so EIP-161 end-of-block state clearing does not reap them
/// together with their storage.
///
/// Only the `NonceManager` storage account qualifies: it persists the 2D nonce channels in the
/// state trie while never being a deployed contract. Sourced from the engine-neutral
/// [`NonceManagerSlots`] so this crate stays revm-free while still sharing one address with the
/// `base_common_precompiles` `NonceManagerStorage::ADDRESS`.
const CODELESS_SYSTEM_ACCOUNTS: [Address; 1] = [NonceManagerSlots::ADDRESS];
```

**File:** crates/execution/eip8130/src/dispatch.rs (L69-92)
```rust
    pub fn authenticate(
        hash: B256,
        authenticator: Address,
        data: &[u8],
    ) -> Result<DispatchOutcome, AuthError> {
        // `address(0)` is the empty / "no actor configured" sentinel and is never
        // a valid authenticator selector; it falls through to `NotCanonical`.
        //
        // secp256k1 is the protocol-reserved native k1 sentinel (`address(1)`);
        // there is no deployed secp256k1 authenticator contract.
        if authenticator == Eip8130Constants::K1_AUTHENTICATOR {
            return Ok(DispatchOutcome::Authenticated { actor_id: Self::ecrecover(hash, data)? });
        }
        if authenticator == Eip8130Contracts::P256_AUTHENTICATOR {
            return Ok(DispatchOutcome::Authenticated { actor_id: Self::p256(hash, data)? });
        }
        if authenticator == Eip8130Contracts::WEBAUTHN_AUTHENTICATOR {
            return Ok(DispatchOutcome::Authenticated { actor_id: Self::webauthn(hash, data)? });
        }
        if authenticator == Eip8130Contracts::DELEGATE_AUTHENTICATOR {
            return Self::delegate(data);
        }
        Err(AuthError::NotCanonical(authenticator))
    }
```

**File:** crates/execution/eip8130/src/account_config.rs (L41-47)
```rust
impl AccountConfigurationStorage<'_> {
    /// Account Configuration system-contract address.
    ///
    /// Pinned to [`Eip8130Contracts::ACCOUNT_CONFIG`]; provisional and tracks the
    /// reference contract's bytecode (see the crate docs).
    pub const ADDRESS: Address = Eip8130Contracts::ACCOUNT_CONFIG;

```

**File:** crates/execution/txpool/src/validator.rs (L1796-1801)
```rust
    /// Returns `true` when an authenticator selector may be used directly on the
    /// EIP-8130 transaction validation path.
    fn authenticator_allowed_for_tx_path(authenticator: &Address) -> bool {
        *authenticator == Eip8130Constants::K1_AUTHENTICATOR
            || Eip8130Contracts::is_canonical_authenticator(authenticator)
    }
```

**File:** crates/execution/eip8130/README.md (L28-31)
```markdown
This is **not** an EVM precompile and does **not** shadow the authenticator addresses:
ordinary EVM `CALL`/`STATICCALL` to those addresses still hits the real deployed
contract bytecode (e.g. `AccountConfiguration.verifySignature()`, `applySignedActorChanges()`
on non-8130 chains, wallet code). The native code here is invoked only by the protocol.
```
