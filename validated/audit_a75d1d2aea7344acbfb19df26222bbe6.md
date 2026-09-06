[1](#0-0) [2](#0-1)

### Citations

**File:** stacks-signer/src/client/stackerdb.rs (L1-1)
```rust
use std::time::Duration;
```

**File:** libsigner/src/v0/messages.rs (L245-283)
```rust
    fn consensus_deserialize<R: Read>(fd: &mut R) -> Result<Self, CodecError> {
        let type_prefix_byte = u8::consensus_deserialize(fd)?;
        let type_prefix = SignerMessageTypePrefix::try_from(type_prefix_byte)?;
        let message = match type_prefix {
            SignerMessageTypePrefix::BlockProposal => {
                let block_proposal = StacksMessageCodec::consensus_deserialize(fd)?;
                SignerMessage::BlockProposal(block_proposal)
            }
            SignerMessageTypePrefix::BlockResponse => {
                let block_response = StacksMessageCodec::consensus_deserialize(fd)?;
                SignerMessage::BlockResponse(block_response)
            }
            SignerMessageTypePrefix::BlockPushed => {
                let block = StacksMessageCodec::consensus_deserialize(fd)?;
                SignerMessage::BlockPushed(block)
            }
            SignerMessageTypePrefix::MockProposal => {
                let message = StacksMessageCodec::consensus_deserialize(fd)?;
                SignerMessage::MockProposal(message)
            }
            SignerMessageTypePrefix::MockSignature => {
                let signature = StacksMessageCodec::consensus_deserialize(fd)?;
                SignerMessage::MockSignature(signature)
            }
            SignerMessageTypePrefix::MockBlock => {
                let block = StacksMessageCodec::consensus_deserialize(fd)?;
                SignerMessage::MockBlock(block)
            }
            SignerMessageTypePrefix::StateMachineUpdate => {
                let state_machine_update = StacksMessageCodec::consensus_deserialize(fd)?;
                SignerMessage::StateMachineUpdate(state_machine_update)
            }
            SignerMessageTypePrefix::BlockPreCommit => {
                let signer_signature_hash = StacksMessageCodec::consensus_deserialize(fd)?;
                SignerMessage::BlockPreCommit(signer_signature_hash)
            }
        };
        Ok(message)
    }
```
