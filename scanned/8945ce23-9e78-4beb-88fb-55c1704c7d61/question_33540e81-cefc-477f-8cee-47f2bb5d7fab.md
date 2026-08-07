[File: 'builtins/src/core_bpf_migration.rs' -> Scope: Critical] [Function: UiAccountData::decode()/to_account() round-trip vs. encode_ui_account, exercised through rpc-client and cli program.rs paths reading migrated accounts (e.g. cli::program::process_dump)] Can a malformed programdata account (data.len() < UpgradeableLoaderState::size_of_programdata_metadata()) returned by a single getAccountInfo(base64) call cause a client-side slice panic at `&programdata_account.data[offset..]` in process_dump-style consumers, demonstrating that the panic surface is not confined to account-decoder but also exists in any code reusing the same offset-without-bounds-check pattern against RPC-returned attacker data? Precondition: attacker-controlled account, single RPC read. Call sequence: getAccountInfo(programdata_pubkey) -> bincode::deserialize header succeeds -> `&data[offset..]` indexing. Invariant tested: decoders handle arbitrary attacker-authored account bytes without panicking (pattern-level, not single-call-site). Scoped impact: repeatable panic pattern reachable via a single RPC response across multiple decoder call sites. Proof idea: grep-driven differential fuzz test applying the same truncated-account corpus to every occurrence of `data[offset..]`/`data[..metadata_size]` slicing on RPC-returned account bytes

### Citations

**File:** builtins/src/core_bpf_migration.rs (L6-42)
```rust
#[derive(Debug, PartialEq)]
pub enum CoreBpfMigrationTargetType {
    /// A standard (stateful) builtin program must have a program account.
    Builtin,
    /// A stateless builtin must not have a program account.
    Stateless,
}

/// Configuration for migrating a built-in program to Core BPF.
#[derive(Debug, PartialEq)]
pub struct CoreBpfMigrationConfig {
    /// The address of the source buffer account to be used to replace the
    /// builtin.
    pub source_buffer_address: Pubkey,
    /// The authority to be used as the BPF program's upgrade authority.
    ///
    /// Note: If this value is set to `None`, then the migration will ignore
    /// the source buffer account's authority. If it's set to any `Some(..)`
    /// value, then the migration will perform a sanity check to ensure the
    /// source buffer account's authority matches the provided value.
    pub upgrade_authority_address: Option<Pubkey>,
    /// The feature gate to trigger the migration to Core BPF.
    /// Note: This feature gate should never be the same as any builtin's
    /// `enable_feature_id`. It should always be a feature gate that will be
    /// activated after the builtin is already enabled.
    pub feature_id: Pubkey,
    /// The type of target to replace.
    pub migration_target: CoreBpfMigrationTargetType,
    /// If specified, the expected verifiable build hash of the bpf program.
    /// This will be checked against the buffer account before migration.
    pub verified_build_hash: Option<Hash>,
    /// Static message used to emit datapoint logging.
    /// This is used to identify the migration in the logs.
    /// Should be unique to the migration, ie:
    ///
