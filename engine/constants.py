"""Canonical rule constants for the base game.

Everything that the official base-game rulebook fixes as a number or a
count lives here, so the rest of the engine reads like the rules.
"""

# --- Resources -------------------------------------------------------------
WOOD = "wood"
BRICK = "brick"
SHEEP = "sheep"
WHEAT = "wheat"
ORE = "ore"

RESOURCES = [WOOD, BRICK, SHEEP, WHEAT, ORE]

# Terrain (tile) types and the resource each produces. The desert produces
# nothing and is where the robber starts.
TERRAIN_FOREST = "forest"      # -> wood
TERRAIN_HILLS = "hills"        # -> brick
TERRAIN_PASTURE = "pasture"    # -> sheep
TERRAIN_FIELDS = "fields"      # -> wheat
TERRAIN_MOUNTAINS = "mountains"  # -> ore
TERRAIN_DESERT = "desert"      # -> nothing
# Expansion/scenario terrains:
TERRAIN_GOLD = "gold"          # gold field -> a random resource per building
TERRAIN_BEANS = "beans"        # casino tile -> beans (only in Gamble mode)

TERRAIN_RESOURCE = {
    TERRAIN_FOREST: WOOD,
    TERRAIN_HILLS: BRICK,
    TERRAIN_PASTURE: SHEEP,
    TERRAIN_FIELDS: WHEAT,
    TERRAIN_MOUNTAINS: ORE,
    TERRAIN_DESERT: None,
    TERRAIN_GOLD: None,         # special-cased in production (random resource)
    TERRAIN_BEANS: None,        # special-cased in production (beans)
}

# How many beans a bean tile pays per adjacent settlement (a city pays double).
BEAN_TILE_PAYOUT = 5

# The 19 tiles of the standard base board.
TERRAIN_COUNTS = {
    TERRAIN_FOREST: 4,
    TERRAIN_HILLS: 3,
    TERRAIN_PASTURE: 4,
    TERRAIN_FIELDS: 4,
    TERRAIN_MOUNTAINS: 3,
    TERRAIN_DESERT: 1,
}

# The 18 number tokens placed on the non-desert tiles. 6 and 8 are the
# "red" high-probability numbers.
NUMBER_TOKENS = [2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12]
RED_NUMBERS = {6, 8}

# Pips printed on each token (number of dots) -> probability weight.
NUMBER_PIPS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}

# --- Building costs ---------------------------------------------------------
COST_ROAD = {WOOD: 1, BRICK: 1}
COST_SETTLEMENT = {WOOD: 1, BRICK: 1, WHEAT: 1, SHEEP: 1}
COST_CITY = {WHEAT: 2, ORE: 3}
COST_DEV_CARD = {WHEAT: 1, SHEEP: 1, ORE: 1}

# --- Piece supply per player -----------------------------------------------
MAX_ROADS = 15
MAX_SETTLEMENTS = 5
MAX_CITIES = 4

# --- Bank -------------------------------------------------------------------
BANK_PER_RESOURCE = 19  # 19 of each resource card in the supply

# --- Development cards ------------------------------------------------------
DEV_KNIGHT = "knight"
DEV_VICTORY_POINT = "victory_point"
DEV_ROAD_BUILDING = "road_building"
DEV_YEAR_OF_PLENTY = "year_of_plenty"
DEV_MONOPOLY = "monopoly"

# 25-card development deck.
DEV_CARD_COUNTS = {
    DEV_KNIGHT: 14,
    DEV_VICTORY_POINT: 5,
    DEV_ROAD_BUILDING: 2,
    DEV_YEAR_OF_PLENTY: 2,
    DEV_MONOPOLY: 2,
}

# --- Ports ------------------------------------------------------------------
PORT_GENERIC = "3:1"  # any 3 identical -> 1 of choice
# 2:1 ports are keyed by the resource they trade.

# The 9 ports of the standard board in clockwise order. "3:1" is generic;
# otherwise the value is the resource for a 2:1 port.
PORT_SEQUENCE = [
    PORT_GENERIC,
    WHEAT,
    ORE,
    PORT_GENERIC,
    SHEEP,
    PORT_GENERIC,
    BRICK,
    WOOD,
    PORT_GENERIC,
]

# --- Victory ----------------------------------------------------------------
VICTORY_POINTS_TO_WIN = 10
VP_SETTLEMENT = 1
VP_CITY = 2
VP_LONGEST_ROAD = 2
VP_LARGEST_ARMY = 2

LONGEST_ROAD_MINIMUM = 5  # need at least 5 segments to claim the card
LARGEST_ARMY_MINIMUM = 3  # need at least 3 knights played to claim the card

# --- Robber -----------------------------------------------------------------
ROBBER_DISCARD_LIMIT = 7  # players with MORE than this discard half on a 7

# --- Casino / "beans" gambling currency ------------------------------------
BEANS_PER_RESOURCE = 20    # 20 beans <-> 1 resource card (both directions)
BEANS_PER_VP = 200         # default beans <-> 1 victory point (host-configurable)
BLACKJACK_DECKS = 6        # a 6-deck shoe
BLACKJACK_PENETRATION = 5  # deal ~5 of 6 decks before reshuffle (cut ~1 deck)
BLACKJACK_MIN_BET = 1      # minimum 1-bean hands
BLACKJACK_PAYOUT_NUM = 3   # natural blackjack pays 3:2
BLACKJACK_PAYOUT_DEN = 2

# --- Players ----------------------------------------------------------------
# Classic player colours; first four match the base game, last two match the
# 5-6 player extension.
PLAYER_COLORS = [
    ("red", "#c0392b"),
    ("blue", "#2c5f9e"),
    ("orange", "#d97a29"),
    ("white", "#e8e4d8"),
    ("green", "#2e8b57"),
    ("brown", "#6e4a2f"),
]
MIN_PLAYERS = 2
MAX_PLAYERS = 6
