import sys
import os
sys.path.append(os.path.abspath("."))
from core.grid_calculator import GridCalculator

gc = GridCalculator()
res = gc.calculate_grid_1h(136.9547, 136.9547, 131.7888, 152.4525)
print(res)
