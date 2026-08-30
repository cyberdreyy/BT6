No vulnerability found for this question.

**Rationale:** The premise that a relayer can mutate the inner `beneficiary_id` post-signature is incorrect. `DelegateAction.actions` (which contains the `DeleteAccountAction` with `beneficiary_id`) is part of the struct hashed by `get_nep461_hash()`, and the signature covers that full hash via `SignedDelegateAction::verify()` / `VersionedSignedDelegateAction::verify()`, checked in `apply_delegate_action` before the receipt is built. [1](#0-0) [2](#0-1) [3](#0-2) 

Since `actions` (containing `DeleteAccountAction.beneficiary_id`) is a field of `DelegateAction`, any mutation of the beneficiary by a relaying party changes the NEP-461 hash and invalidates the signature check, causing `apply_delegate_action` to short-circuit with `DelegateActionInvalidSignature` and never construct the downstream receipt. This is confirmed by the existing unit test `test_delegate_action_signature_verification`, which mutates `receiver_id` (a sibling field of the same struct) post-signing and asserts the exact same failure mode. [4](#0-3) 

The relayer only supplies the outer transaction's `predecessor_id`/`signer_id` (itself, as relayer) and pays fees/gas, but has no ability to alter any field inside the already-signed `DelegateAction`, including `beneficiary_id` of an inner `DeleteAccountAction`, without invalidating the signature verified against the sender's `public_key`. `validate_delete_action`'s narrower scope (`action_validation.rs:399-403`, only validating the account ID format of `beneficiary_id`) is therefore not a gap here — the binding of `beneficiary_id` to the signer's intent is enforced upstream at the signature layer, not expected to be re-validated relationally in `validate_delete_action`. [5](#0-4)

### Citations

**File:** core/primitives/src/action/delegate.rs (L83-90)
```rust
impl SignedDelegateAction {
    pub fn verify(&self) -> bool {
        let delegate_action = &self.delegate_action;
        let hash = delegate_action.get_nep461_hash();
        let public_key = &delegate_action.public_key;

        self.signature.verify(hash.as_ref(), public_key)
    }
```

**File:** core/primitives/src/action/delegate.rs (L344-357)
```rust
impl DelegateAction {
    pub fn get_actions(&self) -> Vec<Action> {
        self.actions.iter().map(|a| a.clone().into()).collect()
    }

    /// Delegate action hash used for NEP-461 signature scheme which tags
    /// different messages before hashing
    ///
    /// For more details, see: [NEP-461](https://github.com/near/NEPs/pull/461)
    pub fn get_nep461_hash(&self) -> CryptoHash {
        let signable = SignableMessage::new(&self, SignableMessageType::DelegateAction);
        let bytes = borsh::to_vec(&signable).expect("Failed to deserialize");
        hash(&bytes)
    }
```

**File:** runtime/runtime/src/actions.rs (L437-461)
```rust
pub(crate) fn apply_delegate_action(
    state_update: &mut TrieUpdate,
    apply_state: &ApplyState,
    action_receipt: &VersionedActionReceipt,
    sender_id: &AccountId,
    signed_delegate_action: VersionedSignedDelegateActionRef<'_>,
    result: &mut ActionResult,
) -> Result<(), RuntimeError> {
    // The inner delegate signature is verified below, here on the receiver shard.
    // Meter its verification compute against this shard's `compute_limit`; the gas
    // for it was already burnt at tx conversion on the signer shard. Without the
    // fix the compute is instead mis-charged on the signer shard (which never runs
    // this verify), letting the work escape the receiver shard's budget. See
    // `signature_verification_cost`.
    if apply_state.config.wasm_config.fix_ml_dsa_cost_charging {
        let verify_compute = delegate_signature_verification_compute(
            &apply_state.config.fees,
            signed_delegate_action.delegate_action().public_key(),
        );
        result.compute_usage = safe_add_compute(result.compute_usage, verify_compute)?;
    }
    if !signed_delegate_action.verify() {
        result.result = Err(ActionErrorKind::DelegateActionInvalidSignature.into());
        return Ok(());
    }
```

**File:** runtime/runtime/src/actions.rs (L1395-1421)
```rust
    #[test]
    fn test_delegate_action_signature_verification() {
        let mut result = ActionResult::default();
        let (action_receipt, mut signed_delegate_action) = create_delegate_action_receipt();
        let sender_id = signed_delegate_action.delegate_action.sender_id.clone();
        let sender_pub_key = signed_delegate_action.delegate_action.public_key.clone();
        let access_key = AccessKey { nonce: 19000000, permission: AccessKeyPermission::FullAccess };

        let apply_state =
            create_apply_state(signed_delegate_action.delegate_action.max_block_height);
        let mut state_update = setup_account(&sender_id, &sender_pub_key, &access_key);

        // Corrupt receiver_id. Signature verification must fail.
        signed_delegate_action.delegate_action.receiver_id = "www.test.near".parse().unwrap();

        apply_delegate_action(
            &mut state_update,
            &apply_state,
            &VersionedActionReceipt::from(action_receipt),
            &sender_id,
            (&signed_delegate_action).into(),
            &mut result,
        )
        .expect("Expect ok");

        assert_eq!(result.result, Err(ActionErrorKind::DelegateActionInvalidSignature.into()));
    }
```
