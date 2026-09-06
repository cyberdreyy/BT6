No vulnerability found for this question.

**Why the equality holds:**

The type-confusion path described does not exist because both the node-side listener and the shared `SignerEvent` decoder route strictly by StackerDB *contract identity*, not by the decoded `SignerMessage` variant, and layer a second type-lane check on top:

1. **Contract-based routing is the outer gate.** `TryFrom<StackerDBChunksEvent> for SignerEvent<T>` first checks `event.contract_id.name` — chunks from the `.miners` contract are only ever decoded as `T` and wrapped in `SignerEvent::MinerMessages`; chunks from a `.signers-X-Y` contract go through a completely separate branch that produces `SignerEvent::SignerMessages`. There is no code path where a chunk delivered under a `.signers-*` contract_id gets interpreted as `MinerMessages`. [1](#0-0) 

2. **Payload-type-to-lane matching is the inner gate.** Inside the `.signers-X-Y` branch, the first byte of the chunk (`SignerMessageTypePrefix`) is checked against the contract's own lane id via `signer_message_payload_matches_lane`, and `SignerMessageTypePrefix::BlockProposal` (and `BlockPushed`, `MockProposal`, `MockBlock`) map to `msg_id() -> None`, so they can **never** match any `MessageSlotID` lane (`BlockResponse`, `StateMachineUpdate`, `BlockPreCommit`). A `BlockProposal`-shaped chunk written to the `.signers-0-1` (BlockResponse) contract is filtered out and logged as "Skipping signer chunk with unexpected payload type for contract" before it is even deserialized into a `SignerMessage`. [2](#0-1) [3](#0-2) [4](#0-3) 

3. **The node's `StackerDBListener` also gates purely on `contract_id`,** not on decoded payload type: it computes `is_signer_event` from `event.contract_id.name.starts_with(SIGNERS_NAME)` before ever calling into `SignerEvent::try_from`, so a chunk arriving on `.miners` is discarded for the signer-message code path regardless of what byte sequence it contains, and vice versa. [5](#0-4) 

4. **This lane-matching mechanism is explicitly tested** to reject every miner-only payload type (`BlockProposal`, `BlockPushed`, `MockProposal`, `MockBlock`) against every `MessageSlotID` lane, confirming the guard is intentional and covers exactly the scenario in the question. [6](#0-5) 

5. Downstream consumers (`stacks-signer/src/v0/signer.rs`) only ever see already-filtered, lane-correct messages: the `SignerEvent::SignerMessages` match arm can only contain `BlockResponse`, `StateMachineUpdate`, or `BlockPreCommit` variants (falling through to `_ => {}` for anything else), and `SignerEvent::MinerMessages` is a structurally distinct variant handled in a separate branch. [7](#0-6) 

Because both the contract-address routing and the payload-type-to-lane check are enforced before any `SignerMessage` reaches consumer logic, a `BlockProposal`-tagged chunk written to a `.signers-0-X` slot is dropped at the `libsigner` decode boundary and never reaches a code path "gated for signer-originated messages only." The premised equality (StackerDB contract/slot the chunk arrived on == the message kind it's trusted to represent) is preserved by construction, so there is no reachable type-confusion, no safety property is broken, and no liveness wedge results.

### Citations

**File:** libsigner/src/events.rs (L547-580)
```rust
    fn try_from(event: StackerDBChunksEvent) -> Result<Self, Self::Error> {
        let received_time = SystemTime::now();
        let signer_event = if event.contract_id.name.as_str() == MINERS_NAME
            && event.contract_id.is_boot()
        {
            let mut messages = vec![];
            for chunk in event.modified_slots {
                match T::consensus_deserialize(&mut chunk.data.as_slice()) {
                    Ok(msg) => messages.push(msg),
                    Err(e) => {
                        debug!(
                            "Signer failed to deserialize miner chunk";
                            "slot_id" => chunk.slot_id,
                            "slot_version" => chunk.slot_version,
                            "data_len" => chunk.data.len(),
                            "error" => %e,
                        );
                    }
                }
            }
            SignerEvent::MinerMessages(messages)
        } else if event.contract_id.name.starts_with(SIGNERS_NAME) && event.contract_id.is_boot() {
            let Some((signer_set, message_id)) =
                get_signers_db_signer_set_message_id(event.contract_id.name.as_str())
            else {
                return Err(EventError::UnrecognizedStackerDBContract(event.contract_id));
            };
            // signer-XXX-YYY boot contract
            //
            // NOTE: the payload-type check below uses v0 `SignerMessageTypePrefix` semantics
            // (the mapping in `signer_message_payload_matches_lane` is fixed to v0). Future
            // signer-message versions must extend that mapping, or their chunks will not be
            // recognized here regardless of which `T` is in scope.
            let messages: Vec<_> = event
```

**File:** libsigner/src/events.rs (L583-614)
```rust
                .filter_map(|chunk| {
                    // Accept only payloads whose type is valid for this contract's message id.
                    let &type_byte = chunk.data.first()?;
                    let payload_kind = SignerMessageTypePrefix::from_u8(type_byte)?;
                    if !signer_message_payload_matches_lane(payload_kind, message_id) {
                        warn!(
                            "Skipping signer chunk with unexpected payload type for contract";
                            "contract" => %event.contract_id,
                            "lane_message_id" => message_id,
                            "payload_type_prefix" => type_byte,
                        );
                        return None;
                    }
                    let Ok(pk) = chunk.recover_pk() else {
                        warn!(
                            "Skipping signer chunk: signature recovery failed";
                            "contract" => %event.contract_id,
                            "slot_id" => chunk.slot_id,
                        );
                        return None;
                    };
                    let Ok(message) = read_next::<T, _>(&mut &chunk.data[..]) else {
                        warn!(
                            "Skipping signer chunk: payload deserialization failed";
                            "contract" => %event.contract_id,
                            "slot_id" => chunk.slot_id,
                        );
                        return None;
                    };
                    Some((chunk.slot_id, pk, message))
                })
                .collect();
```

**File:** libsigner/src/events.rs (L734-749)
```rust
/// Whether a `SignerMessage` payload type is the one expected for the given contract message id.
///
/// `lane_message_id` is the trailing number in the `signers-X-{lane_message_id}` boot
/// contract. Each signer-message contract is dedicated to exactly one `SignerMessage`
/// variant, so the payload's type-prefix byte must map to the same numeric `MessageSlotID`.
///
/// Miner-only payloads (`BlockProposal`, `BlockPushed`, `MockProposal`, `MockBlock`) are not
/// written to a signer contract and never match.
fn signer_message_payload_matches_lane(
    payload_kind: SignerMessageTypePrefix,
    lane_message_id: u32,
) -> bool {
    payload_kind
        .msg_id()
        .is_some_and(|slot| slot.to_u32() == lane_message_id)
}
```

**File:** libsigner/src/events.rs (L798-808)
```rust

        // Miner-only payloads are not written to a signer contract and match nothing.
        for prefix in [BlockProposal, BlockPushed, MockProposal, MockBlock] {
            for slot in MessageSlotID::ALL {
                assert!(
                    !signer_message_payload_matches_lane(prefix, slot.to_u32()),
                    "{prefix:?} should not match {slot:?}"
                );
            }
            assert!(!signer_message_payload_matches_lane(prefix, 0));
        }
```

**File:** libsigner/src/v0/messages.rs (L149-164)
```rust
impl SignerMessageTypePrefix {
    /// The signer-message lane (`MessageSlotID`) this payload type is broadcast on, if any.
    ///
    /// Miner-only payloads (`BlockProposal`, `BlockPushed`, `MockProposal`, `MockBlock`) do
    /// not broadcast over a `.signers-X-Y` contract and return `None`.
    pub fn msg_id(self) -> Option<MessageSlotID> {
        match self {
            // Mock signature uses the same slot as block response since it's exclusively for
            // epoch 2.5 testing.
            Self::BlockResponse | Self::MockSignature => Some(MessageSlotID::BlockResponse),
            Self::StateMachineUpdate => Some(MessageSlotID::StateMachineUpdate),
            Self::BlockPreCommit => Some(MessageSlotID::BlockPreCommit),
            Self::BlockProposal | Self::BlockPushed | Self::MockProposal | Self::MockBlock => None,
        }
    }
}
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L331-346)
```rust
            // check to see if this event we got is a signer event
            let is_signer_event =
                event.contract_id.name.starts_with(SIGNERS_NAME) && event.contract_id.is_boot();

            if !is_signer_event {
                debug!("StackerDBListener: Ignoring StackerDB event for non-signer contract"; "contract" => %event.contract_id);
                continue;
            }

            let modified_slots = &event.modified_slots.clone();

            let Ok(signer_event) = SignerEvent::<SignerMessageV0>::try_from(event).map_err(|e| {
                warn!("StackerDBListener: Failure parsing StackerDB event into signer event. Ignoring message."; "err" => ?e);
            }) else {
                continue;
            };
```

**File:** stacks-signer/src/v0/signer.rs (L519-592)
```rust
            SignerEvent::SignerMessages {
                received_time,
                messages,
                ..
            } => {
                debug!(
                    "{self}: Received {} messages from the other signers",
                    messages.len()
                );
                // try and gather signatures
                for (_slot_id, signer_public_key, message) in messages {
                    let signer_address = StacksAddress::p2pkh(self.mainnet, signer_public_key);
                    if !self.is_valid_signer(&signer_address) {
                        debug!("{self}: Received a message from an unknown signer. Ignoring...";
                            "signer_public_key" => ?signer_public_key,
                            "signer_address" => %signer_address,
                            "message" => ?message,
                        );
                        continue;
                    }
                    match message {
                        SignerMessage::BlockResponse(block_response) => {
                            #[cfg(any(test, feature = "testing"))]
                            if self.test_ignore_all_block_responses(block_response) {
                                continue;
                            }
                            self.handle_block_response(
                                stacks_client,
                                block_response,
                                sortition_state,
                            )
                        }
                        SignerMessage::StateMachineUpdate(update) => self
                            .handle_state_machine_update(signer_public_key, update, received_time),
                        SignerMessage::BlockPreCommit(signer_signature_hash) => {
                            #[cfg(any(test, feature = "testing"))]
                            if self
                                .test_ignore_all_pre_commits(&signer_address, signer_signature_hash)
                            {
                                continue;
                            }
                            self.handle_block_pre_commit(
                                stacks_client,
                                sortition_state,
                                &signer_address,
                                signer_signature_hash,
                            )
                        }
                        _ => {}
                    }
                }
            }
            SignerEvent::MinerMessages(messages) => {
                debug!(
                    "{self}: Received {} messages from the miner",
                    messages.len();
                );
                for message in messages {
                    match message {
                        SignerMessage::BlockProposal(block_proposal) => {
                            #[cfg(any(test, feature = "testing"))]
                            if self.test_ignore_all_block_proposals(block_proposal) {
                                continue;
                            }
                            #[cfg(any(test, feature = "testing"))]
                            if self.test_insert_block_proposal_without_processing(block_proposal) {
                                continue;
                            }
                            self.handle_block_proposal(
                                stacks_client,
                                sortition_state,
                                block_proposal,
                            );
                        }
```
