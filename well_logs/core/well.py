"""Implements Well class."""

from abc import ABCMeta
from copy import copy
from functools import wraps
from collections import Counter

import numpy as np

from .abstract_classes import AbstractWell
from .well_segment import WellSegment


class SegmentDelegatingMeta(ABCMeta):
    """ Metaclass to delegate abstract methods from `Well` to its children
    (instances of `Well` or `WellSegment`). """
    def __new__(mcls, name, bases, namespace):
        abstract_methods = [
            base.__abstractmethods__ for base in bases if hasattr(base, "__abstractmethods__")
        ]
        abstract_methods = frozenset().union(*abstract_methods)
        for name in abstract_methods:
            if name not in namespace:
                namespace[name] = mcls._make_delegator(name)
        return super().__new__(mcls, name, bases, namespace)

    @staticmethod
    def _make_delegator(name):
        @wraps(getattr(WellSegment, name))
        def delegator(self, *args, **kwargs):
            results = []
            for segment in self:
                res = getattr(segment, name)(*args, **kwargs)
                if not isinstance(res, list):
                    res = [res]
                results.extend(res)
            res_well = self.copy()
            res_well.segments = results
            return res_well
        return delegator


class Well(AbstractWell, metaclass=SegmentDelegatingMeta):
    """A class, representing a well.

    Initially an instance of `Well` class consists of a single segment,
    representing the whole well. Subsequently, several `Well` methods, such as
    `crop`, `random_crop` or `drop_nans`, increase the number of segments,
    storing them in a tree-based structure.

    All methods that realized in `WellSegment` are redirected from the well to
    its segments.

    Parameters
    ----------
    path : str or None
        If None, `Well` will be created from `segments`.
        If str, a path to a directory with well data, containing:
        - `meta.json` - a json dict with the following keys:
            - `name` - well name
            - `field` - field name
            - `depth_from` - minimum depth entry in the well logs
            - `depth_to` - maximum depth entry in the well logs
          These values will be stored as instance attributes.
        - `samples_dl` and `samples_uv` (optional) - directories, containing
          daylight and ultraviolet images of core samples respectively. Images
          of the same sample must have the same name in both dirs.
        - Optional `.csv`, `.las` or `.feather` file for certain class
          attributes (see more details in the `Attributes` section).
    core_width : positive float
        The width of core samples in cm. Defaults to 10 cm.
    pixels_per_cm : positive int
        The number of pixels in cm used to determine the loaded width of core
        sample images. Image height is calculated so as to keep the aspect
        ratio. Defaults to 5 pixels.
    segments : list of `WellSegment` or `Well` instances or None
        Segments to put into `segments` attribute. Usually is used by methods
        which increase the tree depth. If None, `path` must be defined.

    Attributes
    ----------
    segments : list of `WellSegment` or `Well` instances or None
        Segments which represent the well and store the data. At the last
        level of nesting it must be `WellSegments`.
    """
    def __init__(self, *args, segments=None, **kwargs):
        super().__init__()
        if segments is None:
            self.segments = [WellSegment(*args, **kwargs)]
        else:
            self.segments = segments

    @property
    def tree_depth(self):
        """Depth of the tree consisting of `Well` and `WellSegment` instances.
        Initial value is equal to 2 because initially all well children are
        `WellSegment` instances.
        """
        if self._has_segments():
            return 2
        return self.segments[0].tree_depth + 1

    @property
    def length(self):
        """float: Length of the well in meters."""
        return self.depth_to - self.depth_from

    @property
    def depth_from(self):
        """float: Top of the well in meters."""
        return min([well.depth_from for well in self])

    @property
    def depth_to(self):
        """float: Bottom of the well in meters."""
        return max([well.depth_to for well in self])

    @property
    def n_segments(self):
        """int: Total number of `WellSegment` instances at last level."""
        return len(self.iter_level())

    def _has_segments(self):
        return all(isinstance(item, WellSegment) for item in self)

    def __iter__(self):
        """Iterator over segments."""
        for segment in self.segments:
            yield segment

    def iter_level(self, level=-1):
        """Iterate over segments at some fixed level.

        Parameters
        ----------
        level : int
            Level of the tree to iterate.

        Returns
        -------
        segments : list of `WellSegment` or `Well` instances or None
            Segments from some level.
        """
        level = level if level >= 0 else self.tree_depth + level
        if (level < 0) or (level > self.tree_depth):
            raise ValueError("Level ({}) can't be negative or exceed tree depth ({})".format(level, self.tree_depth))
        if level == 0:
            return [self]
        if level == 1:
            return self.segments
        return [item for well in self for item in well.iter_level(level - 1)]

    def prune(self):
        """Remove segments without children at last level of the tree.

        Returns
        -------
        self : AbstractWell
            Self with prunned tree.
        """
        # TODO: raise EmptyWellException if no segments left
        self.segments = [well for well in self if isinstance(well, WellSegment) or well.n_segments > 0]
        for well in self:
            if isinstance(well, Well):
                _ = well.prune()
        return self

    def copy(self):
        """Perform shallow copy of an object.

        Returns
        -------
        self : AbstractWell
            Shallow copy.
        """
        return copy(self)

    def dump(self, path):
        """Dump well data. The well will be aggregated and the resulting segment
        will be dumped. Segment attributes are saved in the following manner:
        - `name`, `field`, `depth_from` and `depth_to` attributes are saved in
          `meta.json` file.
        - `core_dl` and `core_uv` are not saved. Instead, `samples_dl` and
          `samples_uv` directories are copied if exist.
        - All other attributes are dumped in feather format.

        Parameters
        ----------
        path : str
            A path to a directory, where well dir with dump will be created.

        Returns
        -------
        self : AbstractWell
            Self unchanged.
        """
        # TODO: aggregate before dumping
        self.segments[0].dump(path)
        return self

    def create_segments(self, src, connected=True):
        """Split segments at last levels. The tree depth will be increased.

        Parameters
        ----------
        src : str or iterable
            Names of attributes to get depthes for splitting. If `src` consists of
            attributes in fdtd format then each row will represent new segment else
            exception will be raised.
        connected : bool
            Join segments which are one after another.

        Returns
        -------
        self : AbstractWell
            Well with splitted segments.
        """
        wells = self.iter_level(-2)
        for well in wells:
            well.segments = [
                Well(segments=segment.create_segments(src, connected)) for segment in well
            ]
        return self

    def crop(self, length, step, drop_last=True):
        """Create crops from segments at last level. All cropped segments have
        the same length and are cropped with some fixed step. The tree depth
        will be increased.

        Parameters
        ----------
        length : int
            Length of each crop in cm.
        step : int
            Step of cropping.
        drop_last : bool
            If True, all segment which are out of image bounds will be dropped.
            If False, the whole segment will be covered by crops. The first crop which
            comes out of segment bounds will remain, the following will be dropped.

        Returns
        -------
        self : AbstractWell
            Well with cropped segments.
        """
        wells = self.iter_level(-2)
        for well in wells:
            well.segments = [
                Well(segments=segment.crop(length, step, drop_last))
                for segment in well
            ]
        return self

    def random_crop(self, length, n_crops=1):
        """Create random crops from the segments at last level. Positions of crops
        are sampled uniformly from segment. The tree depth will be increased. Branches
        at the tree without segemnts at last level will be dropped.

        Parameters
        ----------
        length : int
            Crop length in cm.
        n_crops : int
            Number of crops from the segment.

        Returns
        -------
        segments : list of `WellSegment` instances
            Cropped segments.
        """
        wells = self.iter_level(-2)
        p = np.array([sum([segment.length for segment in item]) for item in wells])
        random_wells = Counter(np.random.choice(wells, n_crops, p=p/sum(p)))
        for well in wells:
            if well in random_wells:
                n_well_crops = random_wells[well]
                p = np.array([item.length for item in well])
                random_segments = Counter(np.random.choice(well.segments, n_well_crops, p=p/sum(p)))
                well.segments = [
                    Well(segments=segment.random_crop(length, n_segment_crops))
                    for segment, n_segment_crops in random_segments.items()
                ]
            else:
                well.segments = []
        return self.prune()

    def drop_nans(self, components_to_drop_nans):
        wells = self.iter_level(-2)
        for well in wells:
            well.segments = [
                Well(segments=segment.drop_nans(components_to_drop_nans)) for segment in well
            ]
        return self.prune()

    def drop_short_segments(self, min_length):
        wells = self.iter_level(-2)
        for well in wells:
            well.segments = [segment for segment in well if segment.length > min_length]
        return self.prune()

    # def assemble_crops(self, crops, name):
    #     i = 0
    #     for segment in self.segments:
    #         for subsegment in segment:
    #             setattr(subsegment, name, crops[i])
    #             i += 1

    # def aggregate(self, name, func):
    #     for i in range(len(self.segments)):
    #         self.segments[i] = [self.segments[i], func([getattr(subsegment, name) for subsegment in self.segments[i]])]
