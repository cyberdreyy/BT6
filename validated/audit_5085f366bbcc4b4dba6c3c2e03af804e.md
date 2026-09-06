[1](#0-0) [2](#0-1)

### Citations

**File:** stacks-signer/src/client/stackerdb.rs (L66-88)
```rust
impl<M: MessageSlotID + 'static> From<&SignerConfig> for StackerDB<M> {
    fn from(config: &SignerConfig) -> Self {
        let mode = match config.signer_mode {
            SignerConfigMode::DryRun => StackerDBMode::DryRun,
            SignerConfigMode::Normal {
                ref signer_slot_id, ..
            } => StackerDBMode::Normal {
                signer_slot_id: *signer_slot_id,
            },
        };
        let signer_db = SignerDb::new(&config.db_path).expect("Failed to connect to SignerDb");

        Self::new(
            &config.node_host,
            config.stacks_private_key.clone(),
            config.mainnet,
            config.reward_cycle,
            signer_db,
            mode,
            config.stackerdb_timeout,
        )
    }
}
```

**File:** stacks-signer/src/v0/signer.rs (L103-113)
```rust
    pub stackerdb: StackerDB<MessageSlotID>,
    /// Whether the signer is a mainnet signer or not
    pub mainnet: bool,
    /// The running mode of the signer (whether dry-run or normal)
    pub mode: SignerMode,
    /// The signer slot ids for the signers in the reward cycle
    pub signer_slot_ids: Vec<SignerSlotID>,
    /// The addresses of other signers
    pub signer_addresses: Vec<StacksAddress>,
    /// The reward cycle this signer belongs to
    pub reward_cycle: u64,
```
