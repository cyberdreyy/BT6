[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** stackslib/src/util_lib/signed_structured_data.rs (L37-46)
```rust
pub fn structured_data_message_hash(structured_data: Value, domain: Value) -> Sha256Sum {
    let message = [
        STRUCTURED_DATA_PREFIX.as_ref(),
        structured_data_hash(domain).as_bytes(),
        structured_data_hash(structured_data).as_bytes(),
    ]
    .concat();

    Sha256Sum::from_data(&message)
}
```

**File:** stackslib/src/util_lib/signed_structured_data.rs (L100-137)
```rust
    pub fn make_pox_4_signer_key_message_hash(
        pox_addr: &PoxAddress,
        reward_cycle: u128,
        topic: &Pox4SignatureTopic,
        chain_id: u32,
        period: u128,
        max_amount: u128,
        auth_id: u128,
    ) -> Sha256Sum {
        let domain_tuple = make_pox_4_signed_data_domain(chain_id);
        let data_tuple = Value::Tuple(
            TupleData::from_data(vec![
                (
                    ClarityName::from_literal("pox-addr"),
                    pox_addr
                        .clone()
                        .as_clarity_tuple()
                        .expect("Error creating signature hash - invalid PoX Address")
                        .into(),
                ),
                (
                    ClarityName::from_literal("reward-cycle"),
                    Value::UInt(reward_cycle),
                ),
                (ClarityName::from_literal("period"), Value::UInt(period)),
                (
                    ClarityName::from_literal("topic"),
                    Value::string_ascii_from_bytes(topic.get_name_str().into()).unwrap(),
                ),
                (ClarityName::from_literal("auth-id"), Value::UInt(auth_id)),
                (
                    ClarityName::from_literal("max-amount"),
                    Value::UInt(max_amount),
                ),
            ])
            .expect("Error creating signature hash"),
        );
        structured_data_message_hash(data_tuple, domain_tuple)
```

**File:** stackslib/src/util_lib/signed_structured_data.rs (L328-339)
```rust
            // Test 3: invalid reward cycle
            let result = call_get_signer_message_hash(
                &mut sim,
                &pox_addr,
                0,
                &topic,
                lock_period,
                &principal,
                max_amount,
                auth_id,
            );
            assert_ne!(expected_hash.clone(), result.as_slice());
```
