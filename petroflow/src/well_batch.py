"""Implements WellBatch class."""
# pylint: disable=abstract-method

import traceback
from functools import wraps
from collections import defaultdict

import numpy as np

from ..batchflow import Batch, SkipBatchException, action, inbatch_parallel, any_action_failed
from ..batchflow.batchflow.batch import MethodsTransformingMeta
from .well import Well
from .base_delegator import BaseDelegator
from .abstract_classes import AbstractWell
from .exceptions import SkipWellException


class WellDelegatingMeta(BaseDelegator, MethodsTransformingMeta):
    """A metaclass to delegate calls to absent abstract methods of a
    `WellBatch` to `Well` objects in `wells` component."""

    @classmethod
    def _create_method(mcls, method, namespace):
        target = namespace["targets"][method]
        namespace[method] = mcls._make_parallel_action(method, target)

    @staticmethod
    def _make_parallel_action(name, target):
        @wraps(getattr(Well, name))
        def delegator(self, well, *args, **kwargs):
            _ = self
            return getattr(well, name)(*args, **kwargs)
        return action(inbatch_parallel(init="wells", post="_filter_assemble", target=target)(delegator))


class WellBatch(Batch, AbstractWell, metaclass=WellDelegatingMeta):
    """A batch class for well data storing and processing.

    Batch class inherits all abstract methods from `Well` class and implements
    some extra functionality. To execute a method for each well in a batch you
    should add it into a pipeline.

    Parameters
    ----------
    index : DatasetIndex
        Unique identifiers of wells in the batch.
    preloaded : tuple, optional
        Data to put in the batch if given. Defaults to `None`.
    kwargs : misc
        Any additional named arguments to `Well.__init__`.

    Attributes
    ----------
    index : DatasetIndex
        Unique identifiers of wells in the batch.
    wells : 1-D ndarray
        An array of `Well` instances.

    Note
    ----
    Some batch methods take `well` as their first argument after `self`. You
    should not specify it in your code since it will be implicitly passed by
    `inbatch_parallel` decorator.
    """

    components = ("wells",)
    targets = defaultdict(lambda: "threads")  # inbatch_parallel target depending on action name

    def __init__(self, index, *args, preloaded=None, **kwargs):
        super().__init__(index, *args, preloaded=preloaded, **kwargs)
        if preloaded is None:
            # Init wells with paths from index
            wells = [Well(self.index.get_fullpath(well), **kwargs) for well in self.indices]
            self.wells = np.array(wells)

    def _filter_assemble(self, results, *args, **kwargs):
        skip_mask = np.array([isinstance(res, SkipWellException) for res in results])
        if sum(skip_mask) == len(self):
            raise SkipBatchException(str(results[0]))
        results = np.array(results)[~skip_mask]  # pylint: disable=invalid-unary-operand-type
        if any_action_failed(results):
            errors = self.get_errors(results)
            print(errors)
            traceback.print_tb(errors[0].__traceback__)
            raise RuntimeError("Could not assemble the batch")
        self.index = self.index.create_subset(self.indices[~skip_mask])  # pylint: disable=invalid-unary-operand-type, attribute-defined-outside-init, line-too-long
        self.wells = results
        return self
