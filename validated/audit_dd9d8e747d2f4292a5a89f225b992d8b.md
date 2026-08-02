[1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** storage/scratchpad/src/sparse_merkle/updater.rs (L288-313)
```rust
pub struct SubTreeUpdater<'a, K, V> {
    depth: usize,
    info: SubTreeInfo,
    updates: &'a [(K, Option<V>)],
    generation: u64,
}

impl<'a, K, V> SubTreeUpdater<'a, K, V>
where
    K: 'a + HashValueRef + Sync,
    V: 'a + HashValueRef + Sync,
{
    pub(crate) fn update(
        root: InMemSubTree,
        updates: &'a [(K, Option<V>)],
        proof_reader: &'a impl ProofRead,
        generation: u64,
    ) -> Result<InMemSubTree> {
        let updater = Self {
            depth: 0,
            info: SubTreeInfo::from_in_mem(&root, generation),
            updates,
            generation,
        };
        Ok(updater.run(proof_reader)?.into_subtree())
    }
```

**File:** storage/scratchpad/src/sparse_merkle/updater.rs (L341-398)
```rust
    fn maybe_end_recursion(self) -> Result<MaybeEndRecursion<InMemSubTreeInfo, Self>> {
        Ok(match self.updates.len() {
            0 => MaybeEndRecursion::End(self.info.materialize(self.generation)),
            1 => {
                let (key_to_update, update) = &self.updates[0];
                match &self.info {
                    SubTreeInfo::InMem(in_mem_info) => match in_mem_info {
                        InMemSubTreeInfo::Empty => match update {
                            Some(value) => {
                                MaybeEndRecursion::End(InMemSubTreeInfo::create_leaf_with_update(
                                    (*key_to_update.hash_ref(), *value.hash_ref()),
                                    self.generation,
                                ))
                            },
                            None => MaybeEndRecursion::End(self.info.materialize(self.generation)),
                        },
                        InMemSubTreeInfo::Leaf { key, .. } => match update {
                            Some(value) => MaybeEndRecursion::or(
                                key == key_to_update.hash_ref(),
                                InMemSubTreeInfo::create_leaf_with_update(
                                    (*key_to_update.hash_ref(), *value.hash_ref()),
                                    self.generation,
                                ),
                                self,
                            ),
                            None => {
                                if key == key_to_update.hash_ref() {
                                    MaybeEndRecursion::End(InMemSubTreeInfo::Empty)
                                } else {
                                    MaybeEndRecursion::End(self.info.materialize(self.generation))
                                }
                            },
                        },
                        _ => MaybeEndRecursion::Continue(self),
                    },
                    SubTreeInfo::Persisted(PersistedSubTreeInfo::Leaf { leaf }) => match update {
                        Some(value) => MaybeEndRecursion::or(
                            leaf.key() == key_to_update.hash_ref(),
                            InMemSubTreeInfo::create_leaf_with_update(
                                (*key_to_update.hash_ref(), *value.hash_ref()),
                                self.generation,
                            ),
                            self,
                        ),
                        None => {
                            if leaf.key() == key_to_update.hash_ref() {
                                MaybeEndRecursion::End(InMemSubTreeInfo::Empty)
                            } else {
                                MaybeEndRecursion::End(self.info.materialize(self.generation))
                            }
                        },
                    },
                    _ => MaybeEndRecursion::Continue(self),
                }
            },
            _ => MaybeEndRecursion::Continue(self),
        })
    }
```
