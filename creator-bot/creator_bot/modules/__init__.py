"""The list of features. To add one, write a Module subclass and add it here.

The order matters: it is the order of the sections in the morning digest.
"""

from .drive import DriveModule
from .ideas import IdeasModule
from .motivation import MotivationModule
from .routines import RoutinesModule
from .videos import VideosModule
from .youtube_stats import YouTubeStatsModule

ALL_MODULES = [
    MotivationModule,
    YouTubeStatsModule,
    VideosModule,
    RoutinesModule,
    IdeasModule,
    DriveModule,
]
