No vulnerability found for this question.

**Rationale:** `enable_staked_oracle_onramp` is gated by an explicit `has_one = admin @ MarginfiError::Unauthorized` constraint on the `group` account in `EnableStakedOracleOnramp`, so only the group's registered admin signer can flip `STAKED_ORACLE_PRICE_USES_ONRAMP`/`STAKED_ORACLE_DISABLED` on `StakedSettings`. [1](#0-0) . There is no permissionless or attacker-controllable path into this instruction — an unprivileged signer attempting it is rejected with `Unauthorized`, which is explicitly exercised in the test suite. [2](#0-1) .

Separately, the downstream pricing logic that consumes the mode (`OnRampTransition::OnRampEnabled` vs `PreTransition` vs `StakeOraclesDisabled`) derives the expected on-ramp account from either the explicit `oracle_keys[3]` or canonically from the bank's stored validator vote account via `derive_staked_onramp_from_vote`, and strictly compares it against the account actually passed in `ais[3]` — this is an exact-identity check (derived key), not a shape/owner-only check, so substituting a same-type oracle account for a different validator's on-ramp is rejected. [3](#0-2) [4](#0-3) .

Since the entrypoint itself cannot be reached by an unprivileged attacker, and the pricing logic already enforces exact oracle-identity (derived) checks rather than mere owner/type compatibility, there's no valid unprivileged path to trigger stale-state mixed pricing here.

### Citations

**File:** programs/marginfi/src/instructions/marginfi_group/on_ramp_transition.rs (L50-58)
```rust
#[derive(Accounts)]
pub struct EnableStakedOracleOnramp<'info> {
    #[account(
        has_one = admin @ MarginfiError::Unauthorized,
    )]
    pub group: AccountLoader<'info, MarginfiGroup>,

    pub admin: Signer<'info>,

```

**File:** tests/specs/staked/s02_addBank.spec.ts (L870-884)
```typescript
  it("(attacker) Tries to enable staked on-ramp oracle pricing - should fail", async () => {
    let tx = new Transaction();
    tx.add(
      await enableStakedOracleOnramp(
        groupAdmin.mrgnBankrunProgram,
        marginfiGroup.publicKey,
        users[0].wallet.publicKey,
      ),
    );
    tx.recentBlockhash = await getBankrunBlockhash(bankrunContext);
    tx.sign(users[0].wallet);
    const result = await banksClient.tryProcessTransaction(tx);
    // Unauthorized
    assertBankrunTxFailed(result, 6042);
  });
```

**File:** programs/marginfi/src/state/price.rs (L93-104)
```rust
fn expected_staked_onramp(bank: &Bank) -> MarginfiResult<Pubkey> {
    if bank.config.oracle_keys[3] != Pubkey::default() {
        return Ok(bank.config.oracle_keys[3]);
    }

    check!(
        bank.integration_acc_1 != Pubkey::default(),
        MarginfiError::StakePoolValidationFailed
    );

    Ok(derive_staked_onramp_from_vote(bank.integration_acc_1))
}
```

**File:** programs/marginfi/src/state/price.rs (L361-382)
```rust
                let sol_pool_adjusted_balance = match bank.on_ramp_transition() {
                    OnRampTransition::OnRampEnabled => {
                        let expected_onramp = expected_staked_onramp(bank)?;
                        if ais[3].key != &expected_onramp {
                            msg!(
                                "Expected staked on-ramp key: {:?}, got: {:?}",
                                expected_onramp,
                                ais[3].key
                            );
                            return Err(error!(MarginfiError::WrongOracleAccountKeys));
                        }

                        let rent = Rent::get()?;
                        staked_pool_net_asset_value(&ais[2], &ais[3], &rent)?
                    }
                    OnRampTransition::PreTransition => {
                        // To be removed once SVSP update is rolled out (likely in 1.10)
                        legacy_staked_pool_delegated_value(&ais[2])?
                    }
                    OnRampTransition::StakeOraclesDisabled => {
                        return Err(error!(MarginfiError::StakeOraclesDisabled));
                    }
```
