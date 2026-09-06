[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5)

### Citations

**File:** stacks-signer/src/signerdb.rs (L527-587)
```rust
static MIGRATE_BLOCKS_TABLE_2_BLOCKS_TABLE_3: &str = r#"
CREATE TABLE IF NOT EXISTS temp_blocks (
    -- The block sighash commits to all of the stacks and burnchain state as of its parent,
    -- as well as the tenure itself so there's no need to include the reward cycle.  Just
    -- the sighash is sufficient to uniquely identify the block across all burnchain, PoX,
    -- and stacks forks.
    signer_signature_hash TEXT NOT NULL PRIMARY KEY,
    reward_cycle INTEGER NOT NULL,
    block_info TEXT NOT NULL,
    consensus_hash TEXT NOT NULL,
    signed_over INTEGER NOT NULL,
    broadcasted INTEGER,
    stacks_height INTEGER NOT NULL,
    burn_block_height INTEGER NOT NULL,
    valid INTEGER,
    state TEXT NOT NULL,
    signed_group INTEGER,
    signed_self INTEGER,
    proposed_time INTEGER NOT NULL,
    validation_time_ms INTEGER,
    tenure_change INTEGER NOT NULL
) STRICT;

INSERT INTO temp_blocks (
    signer_signature_hash,
    reward_cycle,
    block_info,
    consensus_hash,
    signed_over,
    broadcasted,
    stacks_height,
    burn_block_height,
    valid,
    state,
    signed_group,
    signed_self,
    proposed_time,
    validation_time_ms,
    tenure_change
)
SELECT
    signer_signature_hash,
    reward_cycle,
    block_info,
    consensus_hash,
    signed_over,
    broadcasted,
    stacks_height,
    burn_block_height,
    json_extract(block_info, '$.valid') AS valid,
    json_extract(block_info, '$.state') AS state,
    json_extract(block_info, '$.signed_group') AS signed_group,
    json_extract(block_info, '$.signed_self') AS signed_self,
    json_extract(block_info, '$.proposed_time') AS proposed_time,
    json_extract(block_info, '$.validation_time_ms') AS validation_time_ms,
    is_tenure_change(block_info) AS tenure_change
FROM blocks;

DROP TABLE blocks;

ALTER TABLE temp_blocks RENAME TO blocks;"#;
```

**File:** stacks-signer/src/signerdb.rs (L884-942)
```rust
static MIGRATE_BLOCKS_DROP_SIGNED_OVER_ADD_APPROVED_TIME: &str = r#"
CREATE TABLE IF NOT EXISTS new_blocks (
    signer_signature_hash TEXT NOT NULL PRIMARY KEY,
    reward_cycle INTEGER NOT NULL,
    block_info TEXT NOT NULL,
    consensus_hash TEXT NOT NULL,
    broadcasted INTEGER,
    stacks_height INTEGER NOT NULL,
    burn_block_height INTEGER NOT NULL,
    valid INTEGER,
    state TEXT NOT NULL,
    signed_group INTEGER,
    signed_self INTEGER,
    proposed_time INTEGER NOT NULL,
    validation_time_ms INTEGER,
    tenure_change INTEGER NOT NULL,
    tenure_change_cause INTEGER,
    approved_time INTEGER
) STRICT;

INSERT OR IGNORE INTO new_blocks (
    signer_signature_hash,
    reward_cycle,
    block_info,
    consensus_hash,
    broadcasted,
    stacks_height,
    burn_block_height,
    valid,
    state,
    signed_group,
    signed_self,
    proposed_time,
    validation_time_ms,
    tenure_change,
    tenure_change_cause,
    approved_time
)
SELECT
    signer_signature_hash,
    reward_cycle,
    block_info,
    consensus_hash,
    broadcasted,
    stacks_height,
    burn_block_height,
    valid,
    state,
    signed_group,
    signed_self,
    proposed_time,
    validation_time_ms,
    tenure_change,
    tenure_change_cause,
    signed_self
FROM blocks;

DROP TABLE blocks;
ALTER TABLE new_blocks RENAME TO blocks;
```

**File:** stacks-signer/src/signerdb.rs (L1282-1317)
```rust
    /// Register custom scalar functions used by the database
    fn register_scalar_functions(&self) -> Result<(), DBError> {
        // Register helper function for determining if a block is a tenure change transaction
        // Required only for data migration from Schema 4 to Schema 5
        self.db.create_scalar_function(
            "is_tenure_change",
            1,
            FunctionFlags::SQLITE_UTF8 | FunctionFlags::SQLITE_DETERMINISTIC,
            |ctx| {
                let value = ctx.get::<String>(0)?;
                let block_info = serde_json::from_str::<BlockInfo>(&value)
                    .map_err(|e| SqliteError::UserFunctionError(e.into()))?;
                Ok(block_info.is_tenure_change())
            },
        )?;
        // Register helper function for extracting the burn_block from the state machine update content
        // Required only for data migration from Schema 14 to Schema 15
        self.db.create_scalar_function(
            "extract_burn_block_consensus_hash",
            1,
            FunctionFlags::SQLITE_UTF8 | FunctionFlags::SQLITE_DETERMINISTIC,
            |ctx| {
                let json_str = ctx.get::<String>(0)?;
                Self::extract_burn_block_consensus_hash_from_json(&json_str)
            },
        )?;
        Ok(())
    }

    /// Drop registered scalar functions used only for data migrations
    fn remove_scalar_functions(&self) -> Result<(), DBError> {
        self.db.remove_function("is_tenure_change", 1)?;
        self.db
            .remove_function("extract_burn_block_consensus_hash", 1)?;
        Ok(())
    }
```

**File:** stacks-signer/src/signerdb.rs (L1922-1985)
```rust
    /// Record an observed block rejection_signature
    pub fn add_block_rejection_signer_addr(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        addr: &StacksAddress,
        reject_reason: RejectReasonPrefix,
    ) -> Result<bool, DBError> {
        // If this signer/block already has a signature, do not allow a rejection
        let sig_qry = "SELECT EXISTS(SELECT 1 FROM block_signatures WHERE signer_signature_hash = ?1 AND signer_addr = ?2)";
        let sig_args = params![block_sighash, addr.to_string()];
        let exists = self.db.query_row(sig_qry, sig_args, |row| row.get(0))?;
        if exists {
            warn!("Cannot add block rejection because a signature already exists.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %addr,
                "reject_reason" => ?reject_reason
            );
            return Ok(false);
        }

        // Check if a row exists for this sighash/signer combo
        let qry = "SELECT reject_code FROM block_rejection_signer_addrs WHERE signer_signature_hash = ?1 AND signer_addr = ?2 LIMIT 1";
        let args = params![block_sighash, addr.to_string()];
        let existing_code: Option<i64> =
            self.db.query_row(qry, args, |row| row.get(0)).optional()?;

        let reject_code = reject_reason as i64;

        match existing_code {
            Some(code) if code == reject_code => {
                // Row exists with same reject_reason, do nothing
                debug!("Duplicate block rejection.";
                    "signer_signature_hash" => %block_sighash,
                    "signer_address" => %addr,
                    "reject_reason" => ?reject_reason
                );
                Ok(false)
            }
            Some(_) => {
                // Row exists but with different reject_reason, update it
                let update_qry = "UPDATE block_rejection_signer_addrs SET reject_code = ?1 WHERE signer_signature_hash = ?2 AND signer_addr = ?3";
                let update_args = params![reject_code, block_sighash, addr.to_string()];
                self.db.execute(update_qry, update_args)?;
                debug!("Updated block rejection reason.";
                    "signer_signature_hash" => %block_sighash,
                    "signer_address" => %addr,
                    "reject_reason" => ?reject_reason
                );
                Ok(true)
            }
            None => {
                // Row does not exist, insert it
                let insert_qry = "INSERT INTO block_rejection_signer_addrs (signer_signature_hash, signer_addr, reject_code) VALUES (?1, ?2, ?3)";
                let insert_args = params![block_sighash, addr.to_string(), reject_code];
                self.db.execute(insert_qry, insert_args)?;
                debug!("Inserted block rejection.";
                    "signer_signature_hash" => %block_sighash,
                    "signer_address" => %addr,
                    "reject_reason" => ?reject_reason
                );
                Ok(true)
            }
        }
    }
```

**File:** stacks-signer/src/signerdb.rs (L5153-5192)
```rust
        // Verify data survived all migrations
        let block_signed = signer_db
            .block_lookup(&hash_signed)
            .unwrap()
            .expect("Block with signed_self should exist after all migrations");
        assert_eq!(block_signed.block.header.chain_length, 100);
        assert_eq!(block_signed.signed_self, Some(1000));
        assert_eq!(block_signed.state, BlockState::GloballyAccepted);

        let block_unsigned = signer_db
            .block_lookup(&hash_unsigned)
            .unwrap()
            .expect("Block without signed_self should exist after all migrations");
        assert_eq!(block_unsigned.block.header.chain_length, 101);
        assert!(block_unsigned.signed_self.is_none());

        // Database is usable: insert and read back a new block
        let (block_info, block_proposal) = create_block();
        signer_db.insert_block(&block_info).unwrap();
        let retrieved = signer_db
            .block_lookup(&block_proposal.block.header.signer_signature_hash())
            .unwrap()
            .expect("Should retrieve inserted block");
        assert_eq!(BlockInfo::from(block_proposal), retrieved);

        // Reopening is idempotent
        signer_db.remove_scalar_functions().unwrap();
        drop(signer_db);
        let db = SignerDb::new(&db_path).expect("Re-opening should succeed");
        assert_eq!(
            SignerDb::get_schema_version(&db.db).unwrap(),
            SignerDb::SCHEMA_VERSION
        );

        assert_eq!(
            MIGRATIONS.last().unwrap().version.as_u32(),
            SignerDb::SCHEMA_VERSION,
            "Last migration version must match SCHEMA_VERSION"
        );
    }
```
