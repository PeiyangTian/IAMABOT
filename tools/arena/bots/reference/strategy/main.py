from . import *
from .brain import ReferenceStrategy

def get_strategy(team: int) -> Strategy:
    return ReferenceStrategy()
