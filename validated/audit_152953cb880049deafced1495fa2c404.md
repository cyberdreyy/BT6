No vulnerability found for this question.

**Analysis basis:**

`DelegateAction::get_nep461_hash` serializes the *entire* `DelegateAction` struct — including the `actions: Vec<NonDelegateAction>` field — via `SignableMessage::new(&self, ...)` and `borsh::to_vec`, then hashes the result before signature creation/verification. [1](#0-0)  Any mutation to the `actions` field, discriminant or not, changes the borsh byte stream and therefore the NEP-461 hash, which breaks the ed25519 signature check in `SignedDelegateAction::verify`. [2](#0-1) 

There is no intermediate re-serialization step that decouples the hashed/signed bytes from the executed bytes. The `SignedDelegateAction`/`DelegateAction` produced by the client is carried as a single unit — first wrapped into `Action::Delegate` inside the transaction, then copied verbatim into the `ActionReceipt::actions` on the signer's shard, and finally passed unmodified into `apply_delegate_action` on the receiver's shard, where `signed_delegate_action.verify()` is called directly on that same struct before `delegate_action.get_actions()` converts `NonDelegateAction` → `Action` for the new receipt. [3](#0-2)  `apply_action` in the runtime dispatch dispatches `Action::Delegate`/`Action::DelegateV2` straight into `apply_delegate_action` with the receipt's own `signed_delegate_action`, not a freshly re-derived copy. [4](#0-3) 

`NonDelegateAction`'s custom `BorshDeserialize` only rejects nested `Action::Delegate`/`Action::DelegateV2` discriminants to prevent recursive delegation; it does not skip or bypass validation of the wrapped `Action`'s own fields, and it plays no role in mutating already-signed bytes since deserialization happens once, when the original bytes are first parsed — the same bytes that were hashed for the signature. [5](#0-4) 

The existing test suite already covers the "signature must reject any field mutation" property directly, including receiver_id corruption and nonce/version-domain tampering, both of which fail verification as expected. [6](#0-5) [7](#0-6) 

The premise of the question — that some meta-transaction batching or relay logic re-borsh-round-trips the `DelegateAction` such that a relayer-controlled, re-serialized copy with different `actions` is what gets executed instead of what was hashed/signed — is not supported by the code. The struct that is hashed for the NEP-461 signature is the same struct instance that is later read by `get_actions()` for receipt construction; no alternate encoding path or canonicalization ambiguity exists that would let a relayer swap `actions` while preserving the signature.

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

**File:** core/primitives/src/action/delegate.rs (L344-358)
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
}
```

**File:** core/primitives/src/action/delegate.rs (L433-443)
```rust
    impl borsh::de::BorshDeserialize for NonDelegateAction {
        fn deserialize_reader<R: Read>(rd: &mut R) -> ::core::result::Result<Self, Error> {
            match u8::deserialize_reader(rd)? {
                n if DELEGATE_VARIANT_NUMBERS.contains(&n) => Err(Error::new(
                    ErrorKind::InvalidInput,
                    "DelegateAction mustn't contain a nested one",
                )),
                n => borsh::de::EnumExt::deserialize_variant(rd, n).map(Self),
            }
        }
    }
```

**File:** core/primitives/src/action/delegate.rs (L452-487)
```rust
    #[test]
    fn test_signed_delegate_action_v2_verify() {
        let signer = InMemorySigner::test_signer(&"alice.near".parse().unwrap());
        let delegate_action = DelegateActionV2 {
            sender_id: "alice.near".parse().unwrap(),
            receiver_id: "bob.near".parse().unwrap(),
            actions: vec![],
            nonce: TransactionNonce::from_nonce_and_index(1, 3),
            max_block_height: 1000,
            public_key: signer.public_key(),
        };
        let signed = VersionedSignedDelegateAction::sign(&signer, delegate_action.clone().into());
        assert!(signed.verify());

        // A signature bound to nonce index 3 must not verify for another index.
        let forged = VersionedSignedDelegateAction {
            delegate_action: DelegateActionV2 {
                nonce: TransactionNonce::from_nonce_and_index(1, 4),
                ..delegate_action.clone()
            }
            .into(),
            signature: signed.signature,
        };
        assert!(!forged.verify());

        // A signature under the V1 message discriminant must not verify for a
        // V2 action; V1 and V2 signing domains are disjoint.
        let versioned = VersionedDelegateActionPayload::from(delegate_action);
        let v1_tagged_signature =
            SignableMessage::new(&versioned, SignableMessageType::DelegateAction).sign(&signer);
        let forged = VersionedSignedDelegateAction {
            delegate_action: versioned,
            signature: v1_tagged_signature,
        };
        assert!(!forged.verify());
    }
```

**File:** runtime/runtime/src/actions.rs (L437-497)
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
    let delegate_action = signed_delegate_action.delegate_action();
    if apply_state.block_height > delegate_action.max_block_height() {
        result.result = Err(ActionErrorKind::DelegateActionExpired.into());
        return Ok(());
    }
    if delegate_action.sender_id().as_str() != sender_id.as_str() {
        result.result = Err(ActionErrorKind::DelegateActionSenderDoesNotMatchTxReceiver {
            sender_id: delegate_action.sender_id().clone(),
            receiver_id: sender_id.clone(),
        }
        .into());
        return Ok(());
    }

    validate_delegate_action_key(state_update, apply_state, delegate_action, result)?;
    if result.result.is_err() {
        // Validation failed. Need to return Ok() because this is not a runtime error.
        // "result.result" will be return to the User as the action execution result.
        return Ok(());
    }

    // Generate a new receipt from DelegateAction.
    let new_receipt = Receipt::V0(ReceiptV0 {
        predecessor_id: sender_id.clone(),
        receiver_id: delegate_action.receiver_id().clone(),
        receipt_id: CryptoHash::default(),

        receipt: ReceiptEnum::Action(ActionReceipt {
            signer_id: action_receipt.signer_id().clone(),
            signer_public_key: action_receipt.signer_public_key().clone(),
            gas_price: action_receipt.gas_price(),
            output_data_receivers: vec![],
            input_data_ids: vec![],
            actions: delegate_action.get_actions(),
        }),
    });
```

**File:** runtime/runtime/src/actions.rs (L1396-1421)
```rust
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

**File:** runtime/runtime/src/lib.rs (L727-748)
```rust
            Action::Delegate(signed_delegate_action) => {
                metrics::ACTION_CALLED_COUNT.delegate.inc();
                apply_delegate_action(
                    state_update,
                    apply_state,
                    action_receipt,
                    account_id,
                    signed_delegate_action.as_ref().into(),
                    &mut result,
                )?;
            }
            Action::DelegateV2(signed_delegate_action) => {
                metrics::ACTION_CALLED_COUNT.delegate.inc();
                apply_delegate_action(
                    state_update,
                    apply_state,
                    action_receipt,
                    account_id,
                    signed_delegate_action.as_ref().into(),
                    &mut result,
                )?;
            }
```
