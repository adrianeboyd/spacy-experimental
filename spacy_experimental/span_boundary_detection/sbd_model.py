from thinc.types import Floats2d, Floats1d
import torch

from typing import List
from thinc.api import Model, chain, PyTorchWrapper, Maxout, with_padded, list2ragged, with_array
from thinc.types import Floats2d

from spacy.util import registry
from spacy.tokens import Doc
from thinc.util import torch2xp
from thinc.api import ArgsKwargs
from numpy import float32


@registry.architectures("spacy-experimental.PyTorchSpanBoundaryDetection.v1")
def build_boundary_model(
    tok2vec: Model[List[Doc], List[Floats2d]],
    scorer: Model[Floats2d, Floats2d],
    hidden_size: int,
    window_size: int,
) -> Model[List[Doc], Floats2d]:

    pytorch_model = PyTorchWrapper(
        PytorchTokenFeaturer(), convert_inputs=convert_inputs
    )
    pytorch_model.attrs["window_size"] = window_size

    model = chain(
        tok2vec,
        pytorch_model,
        Maxout(nO=hidden_size, normalize=True),
        scorer,
    )
    model.set_ref("tok2vec", tok2vec)
    model.set_ref("scorer", scorer)

    return model


class PytorchTokenFeaturer(torch.nn.Module):
    """
    A single-layer that computes new token vectors based on surrounding tokens
    The resulting token vector look like this (token_vector, mean(surrounding tokens), max(surrounding tokens))
    """

    def __init__(self):
        super(PytorchTokenFeaturer, self).__init__()

    def forward(self, input: List[Floats2d]) -> Floats2d:

        modified_vectors = []

        # Iterate over docs
        for token in input:

            # Calculate features
            token_max = torch.max(token, dim=0)
            token_mean = torch.mean(token, dim=0)
            token_cat = torch.cat((token[0, :], token_mean, token_max.values), dim=0)

            # Add to list
            modified_vectors.append(token_cat)

        modified_vectors = torch.stack(modified_vectors)

        return modified_vectors


def convert_inputs(model: Model, X, is_train: bool):

    window_size = model.attrs["window_size"]
    lengths = [len(x) for x in X]

    converted_input = _get_window_sized_tokens(X, window_size)

    def backprop(dXtorch):
        original_tokens = []
        for token_batch in dXtorch.args:
            for token in token_batch:
                original_tokens.append(token[0])
        original_tokens = torch.stack(original_tokens)

        original_tokens_xp = torch2xp(original_tokens)

        offset = 0
        original_tokens_xp_list = []
        for length in lengths:
            original_tokens_xp_list.append(original_tokens_xp[offset : offset + length])
            offset += length

        return original_tokens_xp_list

    return ArgsKwargs(args=(converted_input,), kwargs={}), backprop


def _get_window_sized_tokens(input: List[Floats2d], window_size: int) -> List[Floats2d]:
    """Create lists of tensors for each token inside the window_size for every token in the doc"""
    modified_vectors = []
    vector_count = (window_size * 2) + 1

    # Iterate over docs
    for doc in input:
        # Iterate over token vectors
        for i, token_vector in enumerate(doc):
            token_tensor = torch.tensor(token_vector, dtype=torch.float32)
            window_vectors = [token_tensor]
            _min = window_size
            _max = window_size

            if i + _max >= len(doc):
                _max = (len(doc) - i) - 1

            if i - _min < 0:
                _min = i

            # Add window tokens
            for k in range(i - _min, i + _max + 1):
                if i != k:
                    tensor = torch.tensor(doc[k], dtype=torch.float32)
                    window_vectors.append(tensor)

            # Fill gaps
            for j in range(0, vector_count - len(window_vectors)):
                window_vectors.append(token_tensor)

            modified_vectors.append(torch.stack(window_vectors))

    modified_vectors = torch.stack(modified_vectors)
    modified_vectors.requires_grad = True
    return modified_vectors


@registry.architectures("spacy-experimental.SpanBoundaryDetection.v1")
def build_boundary_model_v2(
    tok2vec: Model[List[Doc], List[Floats2d]],
    scorer: Model[Floats2d, Floats2d]
) -> Model[List[Doc], Floats2d]:

    logistic_layer = with_array(scorer)

    model = chain(
        tok2vec,
        logistic_layer,
        flattener()
    )

    model.set_ref("tok2vec", tok2vec)
    model.set_ref("scorer", scorer)
    model.set_ref("logistic_layer", logistic_layer)

    return model


def flattener() -> Model[Floats1d,Floats1d]:
    def forward(model: Model[Floats1d,Floats1d], X, is_train:bool) -> Floats1d:
        output = []
        lengths = []

        for doc in X:
            lengths.append(len(doc))
            for token in doc:
                output.append(token)
        output = model.ops.asarray(output, dtype=float32)

        def backprop(Y) -> Floats2d:
            offset = 0
            original = []
            for length in lengths:
                original.append(Y[offset : offset + length])
                offset += length
            return original


        return output, backprop

    return Model("Flattener",forward=forward)

