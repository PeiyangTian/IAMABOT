import os

from . import *
from .brain import AdvancedStrategy
from . import opponents


def get_strategy(team: int) -> Strategy:
    name = os.environ.get("OPP", "advanced").lower()
    table = {
        "advanced": AdvancedStrategy,
        "hunter": opponents.Hunter,
        "thief": opponents.Thief,
        "camper": opponents.Camper,
        "greedy": opponents.Greedy,
        "st24": opponents.SyntaxTerror,
        "gang2": opponents.Gang,
        "terry7": opponents.ZoneHugger,
        "noey": opponents.Noeyedeer,
        "gangx": opponents.GangAllIn,
        "escort": opponents.Escort,
        "cover": opponents.PayloadCover,
        "dibsfa": opponents.DibsfaConvert,
        "dibsfa_turtle": opponents.DibsfaTurtle,
        "gang_turtle": opponents.GangTurtle,
        "clanker_sustain": opponents.ClankerSustain,
        "teamname_push": opponents.TeamNamePush,
        "potatoes_econ": opponents.PotatoesEcon,
    }
    return table[name]()
