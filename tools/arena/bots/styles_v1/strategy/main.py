import os

from . import *
from .brain import AdvancedStrategy
from . import opponents


def get_strategy(team: int) -> Strategy:
    name = os.environ.get("OPP", "advanced").lower()
    table = {
        "advanced": AdvancedStrategy,
        "sniper": opponents.Sniper,
        "raider": opponents.Raider,
        "hunter": opponents.Hunter,
        "stacker": opponents.Stacker,
        "thief": opponents.Thief,
    }
    return table[name]()
