from datasets import load_dataset
from functools import partial, reduce
from typing import Tuple, Iterable, Callable
from torchtyping import TensorType

import torch
from torch.utils.data import DataLoader
from framework.data.ppo_types import PPORLBatch, PPORLElement

from framework.pipeline import BasePipeline, BaseRolloutStore, register_datapipeline
from framework.data.sentiment import SentimentGeneralElement, SentimentRLElement
from framework.data.accelerate_base_datatypes import PromptBatch, PromptElement

@register_datapipeline
class PPOPipeline(BasePipeline):
    def __init__(self, tokenizer, config, prompt_dataset_path = None):
        super().__init__()

        ds = load_dataset('imdb', split='test')
        ds = ds.rename_columns({'text': 'review', 'label': 'sentiment'})
        ds = ds.filter(lambda x: len(x["review"])<500, batched=False)

        self.text = ds['review']
        self.tokens = [tokenizer([text])['input_ids'][0] for text in self.text]

    def __getitem__(self, index : int) -> PromptElement:
        return PromptElement(self.text[index], self.tokens[index])
    
    def __len__(self) -> int:
        return len(self.text)

    def create_loader(self, batch_size : int, shuffle : bool, prep_fn : Callable = None, num_workers : int = 0) -> DataLoader:
        #TODO(dahoas): Decide how to support varying sizes of prompts without having to tokenize on fly
        def collate_fn(elems : Iterable[PromptElement]) -> PromptElement:
            return PromptBatch(
                [elem.text for elem in elems], torch.stack([torch.tensor(elem.tokens).long().flatten() for elem in elems])  # Assumes token tensors all same size
            )
        
        return DataLoader(self, batch_size, shuffle, collate_fn = collate_fn, num_workers = num_workers)


class PPORolloutStorage(BaseRolloutStore):
    def __init__(self):
        super().__init__()

        self.history : Iterable[PPORLElement] = [None]  # Initialize dummy entry to be loaded by accelerate
    
    def push(self, exps : Iterable[PPORLElement]):
        self.history += exps

    def clear_history(self):
        self.history = []

    def __getitem__(self, index : int) -> PPORLElement:
        return self.history[index]

    def __len__(self) -> int:
        return len(self.history)
    
    def create_loader(self, batch_size : int, shuffle : bool, prep_fn : Callable = None, num_workers : int = 0) -> DataLoader:
        """
        Create dataloader for the sentiment task.

        :param prep_fn: Should be tokenizing function
        :type prep_fn: Callable[Iterable[str], Dict[str, torch.tensor]]
        """
        #TODO(dahoas): Decide how to support varying sizes of prompts without having to tokenize on fly
        def collate_fn(elems : Iterable[PPORLElement]):
            res = PPORLBatch(
                    torch.stack([elem.query_tensor for elem in elems]),  # Assumes token tensors all same size
                    torch.stack([elem.response_tensor for elem in elems]),
                    torch.stack([elem.logprobs for elem in elems]),
                    torch.stack([elem.values for elem in elems]),
                    torch.stack([elem.rewards for elem in elems])
            )
            if prep_fn is not None:
                return prep_fn(res)
            else:
                return res
        
        return DataLoader(self, batch_size, shuffle, collate_fn = collate_fn, num_workers = num_workers)