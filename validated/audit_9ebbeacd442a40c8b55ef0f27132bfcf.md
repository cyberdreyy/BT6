No vulnerability found for this question.

**Rationale:** The premise conflates unrelated code. `ConfiguredSigner::not_registered` in `stacks-signer/src/runloop.rs` is a trivial enum constructor with no weight computation, no BlockProposal handling, and no rounding math at all — it simply marks that the local signer is not part of the reward set for a given cycle. [1](#0-0) 

`get_signers_weights` (in `stackslib/src/chainstate/nakamoto/signer_set.rs`) just reads signer/weight tuples from the `.signers` boot contract via read-only Clarity calls — it performs no threshold/rounding arithmetic itself. [2](#0-1) 

The actual rounding logic lives in `NakamotoBlockHeader::compute_voting_weight_threshold`, and it rounds the *required threshold* **up** (ceiling), which makes the bar for approval stricter, not weaker — the opposite of "finalizing below threshold": [3](#0-2) 

This is confirmed as intentional, tested behavior (e.g. 511 total weight → threshold 358, which is `ceil(511*0.7)`, a stricter requirement than a flat floor would give): [4](#0-3) 

There is no code path by which a single attacker-controlled signer slot plus gossiped minority-weight messages can cause aggregated weight to be double-counted or the threshold to be miscomputed downward through `not_registered` or `get_signers_weights`. The exploit idea as stated does not correspond to any real code path in this repository.

### Citations

**File:** stacks-signer/src/runloop.rs (L164-171)
```rust
    /// Create a `NotRegistered` instance of the enum (so that callers do not need
    ///  to supply phantom_state data).
    pub fn not_registered(cycle: u64) -> Self {
        Self::NotRegistered {
            cycle,
            _phantom_state: std::marker::PhantomData,
        }
    }
```

**File:** stackslib/src/chainstate/nakamoto/signer_set.rs (L1076-1145)
```rust
    pub fn get_signers_weights(
        chainstate: &mut StacksChainState,
        sortdb: &SortitionDB,
        block_id: &StacksBlockId,
        reward_cycle: u64,
    ) -> Result<HashMap<StacksAddress, u64>, ChainstateError> {
        let signers_opt = chainstate
            .eval_boot_code_read_only(
                sortdb,
                block_id,
                SIGNERS_NAME,
                &format!("(get-signers u{reward_cycle})"),
            )?
            .expect_optional()
            .map_err(|_| ChainstateError::Expects("get-signers did not return optional".into()))?;
        let mut signers = HashMap::new();
        if let Some(signers_list) = signers_opt {
            for signer in signers_list
                .expect_list()
                .map_err(|_| ChainstateError::Expects("get-signers did not return a list".into()))?
            {
                let signer_tuple = signer.expect_tuple().map_err(|_| {
                    ChainstateError::Expects(
                        "Signer returned from get-signers is not a tuple".into(),
                    )
                })?;
                let principal_data = signer_tuple
                    .get("signer")
                    .map_err(|_| {
                        ChainstateError::Expects("Failed to get 'signer' from tuple".into())
                    })?
                    .clone()
                    .expect_principal()
                    .map_err(|_| {
                        ChainstateError::Expects("'signer' in tuple is not a principal".into())
                    })?;
                let signer_address = if let PrincipalData::Standard(signer) = principal_data {
                    signer.into()
                } else {
                    return Err(ChainstateError::Expects(
                        "Signer returned from get-signers is not a standard principal".into(),
                    ));
                };
                let weight = u64::try_from(
                    signer_tuple
                        .get("weight")
                        .map_err(|_| {
                            ChainstateError::Expects("Failed to get 'weight' from tuple".into())
                        })?
                        .to_owned()
                        .expect_u128()
                        .map_err(|_| {
                            ChainstateError::Expects("'weight' in tuple is not a u128".into())
                        })?,
                )
                .map_err(|_| {
                    ChainstateError::Expects("Signer weight greater than a u64::MAX".into())
                })?;
                signers.insert(signer_address, weight);
            }
        }
        if signers.is_empty() {
            error!(
                "No signers found for reward cycle";
                "reward_cycle" => reward_cycle,
            );
            return Err(ChainstateError::NoRegisteredSigners(reward_cycle));
        }
        Ok(signers)
    }
```

**File:** stackslib/src/chainstate/nakamoto/mod.rs (L1192-1207)
```rust
    /// Compute the threshold for the minimum number of signers (by weight) required
    /// to approve a Nakamoto block.
    pub fn compute_voting_weight_threshold(total_weight: u32) -> Result<u32, ChainstateError> {
        let threshold = NAKAMOTO_SIGNER_BLOCK_APPROVAL_THRESHOLD;
        let total_weight = u64::from(total_weight);
        let ceil = if (total_weight * threshold) % 10 == 0 {
            0
        } else {
            1
        };
        u32::try_from((total_weight * threshold) / 10 + ceil).map_err(|_| {
            ChainstateError::InvalidStacksBlock(
                "Overflow when computing nakamoto block approval threshold".to_string(),
            )
        })
    }
```

**File:** stackslib/src/chainstate/nakamoto/tests/mod.rs (L4096-4123)
```rust
    #[test]
    pub fn test_compute_voting_weight_threshold() {
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(100_u32).unwrap(),
            70_u32,
        );

        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(10_u32).unwrap(),
            7_u32,
        );

        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(3000_u32).unwrap(),
            2100_u32,
        );

        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(4000_u32).unwrap(),
            2800_u32,
        );

        // Round-up check
        assert_eq!(
            NakamotoBlockHeader::compute_voting_weight_threshold(511_u32).unwrap(),
            358_u32,
        );
    }
```
